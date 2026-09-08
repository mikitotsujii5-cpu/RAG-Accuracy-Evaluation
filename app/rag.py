"""Deterministic RAG pipeline and browser-safe SSE envelopes."""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterator

import mlflow
from mlflow.entities import Document, SpanType
from mlflow.entities.span import LiveSpan, NO_OP_SPAN_TRACE_ID

from errors import AppError, ResourceNotReadyError
from gateway import json_or_value
from repository import Repository


RETURN_COLUMNS = [
    "chunk_id",
    "project_id",
    "document_id",
    "chunk_to_retrieve",
    "parent_chunk_id",
    "parent_chunk_to_retrieve",
    "doc_uri",
    "page_number",
    "page_numbers",
    "parent_page_numbers",
    "title",
    "model",
    "model_year",
    "document_type",
    "vehicle_category",
    "variant_id",
]


@dataclass(slots=True)
class RetrievalBundle:
    rows: list[dict[str, Any]]
    expanded_queries: list[str]
    filters: dict[str, Any]
    elapsed_ms: float
    warnings: list[str]


class CancellationRegistry:
    def __init__(
        self,
        *,
        ttl_seconds: float = 900.0,
        max_entries: int = 4096,
        clock: Any = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._cancelled: dict[str, float] = {}
        self._lock = threading.Lock()

    def request(self, request_id: str) -> None:
        with self._lock:
            now = self._clock()
            self._purge_expired(now)
            self._cancelled[request_id] = now + self._ttl_seconds
            while len(self._cancelled) > self._max_entries:
                oldest = min(self._cancelled, key=self._cancelled.__getitem__)
                del self._cancelled[oldest]

    def is_requested(self, request_id: str) -> bool:
        with self._lock:
            self._purge_expired(self._clock())
            return request_id in self._cancelled

    def clear(self, request_id: str) -> None:
        with self._lock:
            self._cancelled.pop(request_id, None)

    def _purge_expired(self, now: float) -> None:
        expired = [
            request_id
            for request_id, expires_at in self._cancelled.items()
            if expires_at <= now
        ]
        for request_id in expired:
            del self._cancelled[request_id]


class RagOrchestrator:
    def __init__(self, repository: Repository, cancellations: CancellationRegistry) -> None:
        self.repository = repository
        self.cancellations = cancellations

    def retrieve(
        self,
        *,
        project_id: str,
        index_name: str,
        question: str,
        config: dict[str, Any],
        model_target: str,
        user_access_token: str | None = None,
    ) -> RetrievalBundle:
        with mlflow.start_span(
            name="final_retrieval",
            span_type=SpanType.RETRIEVER,
            attributes={
                "project_id": project_id,
                "phase_id": str(config.get("phase_id") or "custom"),
                "variant_id": str(config.get("variant_id") or "unspecified"),
                "query_type": str(config["query_type"]),
                "reranking": bool(config["reranking"]),
            },
        ) as retrieval_span:
            started = time.perf_counter()
            expanded = [question]
            if config["query_optimization"]:
                expanded.extend(
                    self._expand_queries(question, model_target, user_access_token)
                )
            # Keep query order stable and cap expansion to the guide's original + 2.
            expanded = list(dict.fromkeys(query.strip() for query in expanded if query.strip()))[:3]
            variant_id = str(config.get("variant_id") or "").strip()
            if not variant_id:
                raise ResourceNotReadyError(
                    "検索対象のIndex Variantを特定できません。Variantを選び直してください。"
                )
            # Every configured profile can share one physical Index across many
            # Projects and logical Variants. These two server-owned predicates
            # are mandatory in every Phase; metadata filtering can only narrow
            # the already isolated corpus.
            filters: dict[str, Any] = {
                "project_id": project_id,
                "variant_id": variant_id,
            }
            if config["metadata_filtering"]:
                metadata_filters = self.repository.resolve_metadata_filters(
                    question,
                    project_id=project_id,
                )
                filters.update({
                    key: value
                    for key, value in metadata_filters.items()
                    if key not in {"project_id", "variant_id"}
                })
            retrieval_span.set_inputs({
                "query": question,
                "expanded_queries": expanded,
                "filters": filters,
            })
            by_chunk: dict[str, dict[str, Any]] = {}
            warnings: list[str] = []
            for query in expanded:
                raw = self.repository.gateway.similarity_search(
                    index_name=index_name,
                    query_text=query,
                    query_type=config["query_type"],
                    columns=RETURN_COLUMNS,
                    num_results=10,
                    filters=filters,
                    reranking=bool(config["reranking"]),
                )
                rows, debug = parse_search_response(raw)
                debug_warnings = debug.get("warnings") or []
                if isinstance(debug_warnings, str):
                    debug_warnings = [debug_warnings]
                warnings.extend(str(item) for item in debug_warnings)
                for row in rows:
                    if str(row.get("project_id")) != project_id:
                        raise ResourceNotReadyError(
                            "検索結果に別Projectのデータが含まれました。Index設定を確認してください。"
                        )
                    if str(row.get("variant_id")) != variant_id:
                        raise ResourceNotReadyError(
                            "検索結果に別Index Variantのデータが含まれました。Index同期とfilter設定を確認してください。"
                        )
                    chunk_id = str(row["chunk_id"])
                    if chunk_id not in by_chunk:
                        row["matched_query"] = query
                        by_chunk[chunk_id] = row

            # Parent-child variants return one broad context per parent.
            final: list[dict[str, Any]] = []
            seen_parent: set[str] = set()
            for row in by_chunk.values():
                parent_key = str(row.get("parent_chunk_id") or row["chunk_id"])
                if parent_key in seen_parent:
                    continue
                seen_parent.add(parent_key)
                final.append(row)
                if len(final) == 10:
                    break
            retrieval_span.set_outputs(_retrieval_documents(final))
            return RetrievalBundle(
                rows=final,
                expanded_queries=expanded,
                filters=filters,
                elapsed_ms=(time.perf_counter() - started) * 1000,
                warnings=warnings,
            )

    def _expand_queries(
        self,
        question: str,
        model_target: str,
        user_access_token: str | None = None,
    ) -> list[str]:
        prompt = (
            "次の質問をAI Search向けに言い換え、元質問とは異なる検索クエリを最大2件作成してください。"
            "固有名詞、日付、型番、専門用語を維持してください。JSONだけを返してください。"
            '\n形式: {"queries":["...","..."]}\n質問: ' + question
        )
        raw = self.repository.gateway.invoke_model(
            model_target,
            [
                {"role": "system", "content": "あなたは日本語の検索クエリ最適化担当です。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=220,
            user_access_token=user_access_token,
        )
        text, _ = extract_model_text(raw)
        candidate = _extract_json_object(text)
        queries = candidate.get("queries") if isinstance(candidate, dict) else []
        if not isinstance(queries, list):
            return []
        return [str(item)[:1000] for item in queries if isinstance(item, str) and item.strip()][:2]

    def generate_answer(
        self,
        *,
        question: str,
        bundle: RetrievalBundle,
        model_target: str,
        user_access_token: str | None = None,
    ) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
        with mlflow.start_span(
            name="answer_generation",
            span_type=SpanType.CHAT_MODEL,
            attributes={"model_target": model_target},
        ) as generation_span:
            sources = []
            citations = []
            for position, row in enumerate(bundle.rows, start=1):
                pages = _pages(row)
                page = pages[0] if pages else 1
                citation_id = f"S{position}:p{page}"
                context = row.get("parent_chunk_to_retrieve") or row.get("chunk_to_retrieve") or ""
                sources.append(
                    f"[{citation_id}] {row.get('title') or '文書'} / page {page}\n{str(context)[:7000]}"
                )
                citations.append({
                    "citation_id": citation_id,
                    "document_id": str(row["document_id"]),
                    "title": str(row.get("title") or "文書"),
                    "page_numbers": pages or [page],
                })
            if not sources:
                answer = "根拠を確認できませんでした。データ準備と検索条件を確認してください。"
                generation_span.set_inputs({"question": question, "retrieved_context": []})
                generation_span.set_outputs({"answer": answer, "citations": []})
                return answer, {}, []
            prompt = (
                "以下の検索結果だけを根拠として日本語で回答してください。"
                "重要な事実の直後には必ず [S1:p3] の形式で引用IDを付けてください。"
                "検索結果にないことは推測せず『根拠を確認できません』と答えてください。\n\n"
                f"質問:\n{question}\n\n検索結果:\n" + "\n\n".join(sources)
            )
            messages = [
                {"role": "system", "content": "あなたは登録文書を根拠に回答するRAGアシスタントです。"},
                {"role": "user", "content": prompt},
            ]
            generation_span.set_inputs({"messages": messages})
            raw = self.repository.gateway.invoke_model(
                model_target,
                messages,
                max_tokens=1100,
                user_access_token=user_access_token,
            )
            answer, usage = extract_model_text(raw)
            valid_ids = {item["citation_id"] for item in citations}
            mentioned = set(re.findall(r"\[(S\d+:p\d+)\]", answer))
            invalid = mentioned - valid_ids
            if invalid:
                for citation in invalid:
                    answer = answer.replace(f"[{citation}]", "")
            if not (mentioned & valid_ids):
                answer = answer.rstrip() + f"\n\n根拠: [{citations[0]['citation_id']}]"
            citations = referenced_citations(answer, citations)
            generation_span.set_outputs({
                "answer": answer,
                "usage": usage,
                "citations": citations,
            })
            return answer, usage, citations

    async def stream_response(
        self,
        *,
        project_id: str,
        run: dict[str, Any],
        question: str,
        index_name: str,
        model_target: str,
        config: dict[str, Any],
        user_access_token: str | None = None,
    ) -> AsyncIterator[str]:
        request_id = run["request_id"]
        with _rag_request_span(
            request_id=request_id,
            project_id=project_id,
            question=question,
            config=config,
        ) as request_span:
            async for event in self._stream_response_events(
                project_id=project_id,
                run=run,
                question=question,
                index_name=index_name,
                model_target=model_target,
                config=config,
                user_access_token=user_access_token,
                request_span=request_span,
            ):
                yield event

    async def _stream_response_events(
        self,
        *,
        project_id: str,
        run: dict[str, Any],
        question: str,
        index_name: str,
        model_target: str,
        config: dict[str, Any],
        user_access_token: str | None,
        request_span: LiveSpan,
    ) -> AsyncIterator[str]:
        request_id = run["request_id"]
        message_id = run["message_id"]
        sequence = 1
        answer_so_far = ""
        citations: list[dict[str, Any]] = []
        trace_id = _trace_id_from_span(request_span)
        trace_href = self.repository.trace_href(trace_id) if trace_id else None
        started = time.perf_counter()
        terminal_status: str | None = None
        usage: dict[str, Any] = {}

        async def checkpoint() -> str:
            if self.cancellations.is_requested(request_id):
                return "CANCEL_REQUESTED"
            return await asyncio.to_thread(
                self.repository.get_chat_run_status, project_id, request_id
            )

        async def finalize_cancel() -> str:
            partial_citations = referenced_citations(answer_so_far, citations)
            return await asyncio.to_thread(
                self.repository.complete_chat_run,
                project_id=project_id,
                request_id=request_id,
                message_id=message_id,
                answer=answer_so_far,
                citations=partial_citations,
                status="CANCELED",
                trace_id=trace_id,
            )

        def record_terminal(
            status: str,
            *,
            error: dict[str, Any] | None = None,
        ) -> None:
            output: dict[str, Any] = {
                "status": status,
                "answer": answer_so_far,
                "citations": referenced_citations(answer_so_far, citations),
            }
            if usage:
                output["usage"] = usage
            if error:
                output["error"] = error
            if status == "ERROR":
                request_span.set_status("ERROR")
            request_span.set_outputs(output)

        def terminal_event(
            status: str,
            *,
            error: dict[str, Any] | None = None,
        ) -> str:
            if status == "COMPLETED":
                return sse_event(
                    "run.completed",
                    request_id,
                    sequence,
                    {
                        "message_id": message_id,
                        "elapsed_ms": round(
                            (time.perf_counter() - started) * 1000, 1
                        ),
                        "usage": usage,
                        "trace_id": trace_id,
                        "trace_href": trace_href,
                    },
                )
            if status == "CANCELED":
                return sse_event(
                    "run.cancelled",
                    request_id,
                    sequence,
                    {"message_id": message_id},
                )
            payload = error or {
                "code": "INTERNAL_ERROR",
                "message": "回答生成中にエラーが発生しました。しばらくしてから再実行してください。",
                "retryable": True,
            }
            return sse_event("run.error", request_id, sequence, payload)

        async def resolve_checkpoint(status: str) -> str | None:
            if status in {"CANCEL_REQUESTED", "CANCELED"}:
                status = await finalize_cancel()
            return status if status in {"COMPLETED", "CANCELED", "ERROR"} else None

        try:
            yield sse_event(
                "run.started", request_id, sequence,
                {"message_id": message_id, "reused": bool(run.get("reused"))},
            )
            sequence += 1

            # A completed HTTP retry is replayed from its durable message. It
            # never launches another model call and still uses the established
            # SSE event contract.
            reused_status = str(run.get("status") or "").upper()
            if run.get("reused") and reused_status in {"COMPLETED", "CANCELED", "ERROR"}:
                answer_so_far = str(run.get("answer") or "")
                stored_citations = run.get("citations")
                citations = stored_citations if isinstance(stored_citations, list) else []
                if answer_so_far:
                    yield sse_event(
                        "response.delta", request_id, sequence, {"text": answer_so_far}
                    )
                    sequence += 1
                for citation in citations:
                    yield sse_event(
                        "citation.added",
                        request_id,
                        sequence,
                        _citation_event_payload(project_id, citation),
                    )
                    sequence += 1
                terminal_status = reused_status
                record_terminal(terminal_status)
                yield terminal_event(terminal_status)
                return

            if trace_id:
                yield sse_event(
                    "trace.available", request_id, sequence,
                    {"trace_id": trace_id, "href": trace_href},
                )
                sequence += 1

            terminal_status = await resolve_checkpoint(await checkpoint())
            if terminal_status:
                record_terminal(terminal_status)
                yield terminal_event(terminal_status)
                return

            bundle = await asyncio.to_thread(
                self.retrieve,
                project_id=project_id,
                index_name=index_name,
                question=question,
                config=config,
                model_target=model_target,
                user_access_token=user_access_token,
            )
            terminal_status = await resolve_checkpoint(await checkpoint())
            if terminal_status:
                record_terminal(terminal_status)
                yield terminal_event(terminal_status)
                return

            evidence = []
            for rank, row in enumerate(bundle.rows, start=1):
                pages = _pages(row)
                document_id = str(row.get("document_id") or "")
                page = pages[0] if pages else None
                href = f"/api/projects/{project_id}/documents/{document_id}/content"
                if page is not None:
                    href += f"#page={page}"
                evidence.append({
                    "rank": rank,
                    "title": row.get("title") or "文書",
                    "document_id": document_id,
                    "page": page,
                    "href": href,
                    "matched_query": row.get("matched_query"),
                })
            yield sse_event(
                "retrieval.completed", request_id, sequence,
                {
                    "result_count": len(bundle.rows),
                    "expanded_queries": bundle.expanded_queries,
                    "filters": bundle.filters,
                    "elapsed_ms": round(bundle.elapsed_ms, 1),
                    "warnings": bundle.warnings,
                    "evidence": evidence,
                },
            )
            sequence += 1

            terminal_status = await resolve_checkpoint(await checkpoint())
            if terminal_status:
                record_terminal(terminal_status)
                yield terminal_event(terminal_status)
                return

            answer, usage, citations = await asyncio.to_thread(
                self.generate_answer,
                question=question,
                bundle=bundle,
                model_target=model_target,
                user_access_token=user_access_token,
            )
            terminal_status = await resolve_checkpoint(await checkpoint())
            if terminal_status:
                record_terminal(terminal_status)
                yield terminal_event(terminal_status)
                return

            for part in _text_chunks(answer, 20):
                if self.cancellations.is_requested(request_id):
                    terminal_status = await finalize_cancel()
                    partial_citations = referenced_citations(answer_so_far, citations)
                    for citation in partial_citations:
                        yield sse_event(
                            "citation.added", request_id, sequence,
                            _citation_event_payload(project_id, citation),
                        )
                        sequence += 1
                    record_terminal(terminal_status)
                    yield terminal_event(terminal_status)
                    return
                answer_so_far += part
                yield sse_event(
                    "response.delta", request_id, sequence, {"text": part}
                )
                sequence += 1
                await asyncio.sleep(0)
            for citation in citations:
                yield sse_event(
                    "citation.added", request_id, sequence,
                    _citation_event_payload(project_id, citation),
                )
                sequence += 1
            terminal_status = await resolve_checkpoint(await checkpoint())
            if terminal_status:
                record_terminal(terminal_status)
                yield terminal_event(terminal_status)
                return

            actual_status = await asyncio.to_thread(
                self.repository.complete_chat_run,
                project_id=project_id,
                request_id=request_id,
                message_id=message_id,
                answer=answer,
                citations=citations,
                status="COMPLETED",
                trace_id=trace_id,
            )
            if actual_status == "CANCEL_REQUESTED":
                actual_status = await finalize_cancel()
            terminal_status = actual_status
            record_terminal(terminal_status)
            yield terminal_event(terminal_status)
        except AppError as exc:
            actual_status = await asyncio.to_thread(
                self._safe_fail, project_id, request_id, message_id, trace_id
            )
            if actual_status == "CANCEL_REQUESTED":
                actual_status = await finalize_cancel()
            terminal_status = actual_status if actual_status in {
                "COMPLETED", "CANCELED", "ERROR"
            } else "ERROR"
            error = {
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
            }
            record_terminal(terminal_status, error=error if terminal_status == "ERROR" else None)
            yield terminal_event(
                terminal_status,
                error=error if terminal_status == "ERROR" else None,
            )
        except asyncio.CancelledError:
            if terminal_status not in {"COMPLETED", "CANCELED", "ERROR"}:
                terminal_status = await asyncio.shield(finalize_cancel())
                record_terminal(terminal_status)
            raise
        except GeneratorExit:
            if terminal_status not in {"COMPLETED", "CANCELED", "ERROR"}:
                terminal_status = await asyncio.shield(finalize_cancel())
                record_terminal(terminal_status)
            raise
        except Exception:
            actual_status = await asyncio.to_thread(
                self._safe_fail, project_id, request_id, message_id, trace_id
            )
            if actual_status == "CANCEL_REQUESTED":
                actual_status = await finalize_cancel()
            terminal_status = actual_status if actual_status in {
                "COMPLETED", "CANCELED", "ERROR"
            } else "ERROR"
            error = {
                "code": "INTERNAL_ERROR",
                "message": "回答生成中にエラーが発生しました。しばらくしてから再実行してください。",
                "retryable": True,
            }
            record_terminal(terminal_status, error=error if terminal_status == "ERROR" else None)
            yield terminal_event(
                terminal_status,
                error=error if terminal_status == "ERROR" else None,
            )
        finally:
            self.cancellations.clear(request_id)

    def _safe_fail(
        self,
        project_id: str,
        request_id: str,
        message_id: str,
        trace_id: str | None,
    ) -> str | None:
        try:
            return self.repository.fail_chat_run(
                project_id, request_id, message_id, trace_id=trace_id
            )
        except Exception:
            return None


@contextmanager
def _rag_request_span(
    *,
    request_id: str,
    project_id: str,
    question: str,
    config: dict[str, Any],
) -> Iterator[LiveSpan]:
    phase_id = str(config.get("phase_id") or "custom")
    variant_id = str(config.get("variant_id") or "unspecified")
    with mlflow.start_span(
        name="rag_request",
        span_type=SpanType.AGENT,
        attributes={
            "request_id": request_id,
            "project_id": project_id,
            "phase_id": phase_id,
            "variant_id": variant_id,
        },
    ) as span:
        span.set_inputs({
            "question": question,
            "retrieval_config": {
                "query_type": config["query_type"],
                "metadata_filtering": bool(config["metadata_filtering"]),
                "reranking": bool(config["reranking"]),
                "query_optimization": bool(config["query_optimization"]),
            },
        })
        set_rag_trace_context(
            project_id=project_id,
            phase_id=phase_id,
            variant_id=variant_id,
            request_id=request_id,
        )
        yield span


def set_rag_trace_context(
    *,
    project_id: str,
    phase_id: str,
    variant_id: str,
    request_id: str | None = None,
) -> None:
    """Attach the stable RAG dimensions to the active MLflow trace and root span."""
    active_span = mlflow.get_current_active_span()
    if active_span is None:
        return
    tags = {
        "project_id": str(project_id),
        "phase_id": str(phase_id),
        "variant_id": str(variant_id),
    }
    active_span.set_attributes(tags)
    mlflow.update_current_trace(
        tags=tags,
        client_request_id=request_id,
    )


def referenced_citations(
    answer: str,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return only citations that occur in the answer, in first-use order."""
    by_id = {str(item.get("citation_id")): item for item in candidates}
    referenced_ids = dict.fromkeys(re.findall(r"\[(S\d+:p\d+)\]", answer))
    return [by_id[citation_id] for citation_id in referenced_ids if citation_id in by_id]


def _trace_id_from_span(span: LiveSpan) -> str | None:
    trace_id = getattr(span, "trace_id", None)
    if not trace_id or trace_id == NO_OP_SPAN_TRACE_ID:
        return None
    return str(trace_id)


def _citation_event_payload(
    project_id: str,
    citation: dict[str, Any],
) -> dict[str, Any]:
    pages = citation.get("page_numbers") or []
    page = pages[0] if pages else None
    document_id = str(citation.get("document_id") or "")
    # Link to the authenticated PDF response itself. The fragment is handled
    # by the browser PDF viewer and is not sent to the server.
    href = f"/api/projects/{project_id}/documents/{document_id}/content"
    if page is not None:
        href += f"#page={page}"
    return {
        "citation_id": citation.get("citation_id"),
        # Keep the validated document identity in the event payload.  The
        # browser uses this value to rebuild the authenticated PDF URL instead
        # of trusting an arbitrary model-provided link.
        "document_id": document_id,
        "title": citation.get("title") or "文書",
        "page_number": page,
        "href": href,
    }


def _retrieval_documents(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    documents = []
    for rank, row in enumerate(rows, start=1):
        content = row.get("parent_chunk_to_retrieve") or row.get("chunk_to_retrieve") or ""
        metadata = {
            "rank": rank,
            "document_id": str(row["document_id"]),
            "title": str(row.get("title") or "文書"),
            "page_numbers": _pages(row),
            "matched_query": str(row.get("matched_query") or ""),
        }
        for key in ("doc_uri", "model", "model_year", "document_type", "vehicle_category"):
            if row.get(key) is not None:
                metadata[key] = str(row[key])
        documents.append(Document(
            page_content=str(content),
            metadata=metadata,
            id=str(row["chunk_id"]),
        ).to_dict())
    return documents


def parse_search_response(raw: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest = raw.get("manifest") or {}
    result = raw.get("result") or {}
    columns = manifest.get("columns") or (manifest.get("schema") or {}).get("columns") or []
    names = [column.get("name") for column in columns if isinstance(column, dict)]
    arrays = result.get("data_array") or []
    if not names:
        raise ResourceNotReadyError("AI Search応答に列定義がありません。Index列を確認してください。")
    required = {
        "chunk_id",
        "project_id",
        "variant_id",
        "document_id",
        "chunk_to_retrieve",
        "title",
    }
    if missing := sorted(required - set(names)):
        raise ResourceNotReadyError("AI Search Indexに必要な列がありません: " + "、".join(missing))
    rows = []
    for values in arrays:
        if not isinstance(values, list) or len(values) != len(names):
            raise ResourceNotReadyError("AI Search応答の列数が一致しません。")
        rows.append(dict(zip(names, values, strict=True)))
    debug = raw.get("debug_info") or {}
    return rows, debug if isinstance(debug, dict) else {}


def extract_model_text(raw: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    choices = raw.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        content = message.get("content") or choice.get("text")
        if isinstance(content, str) and content.strip():
            return content.strip(), usage
    for key in ("output_text", "answer", "text"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), usage
    predictions = raw.get("predictions")
    if isinstance(predictions, list) and predictions:
        first = predictions[0]
        if isinstance(first, str):
            return first.strip(), usage
        if isinstance(first, dict):
            for key in ("content", "text", "answer"):
                if isinstance(first.get(key), str):
                    return first[key].strip(), usage
    raise ResourceNotReadyError("LLM応答から回答テキストを取得できませんでした。")


def sse_event(event_type: str, request_id: str, sequence: int, payload: dict[str, Any]) -> str:
    envelope = {
        "schema_version": "1.0",
        "request_id": request_id,
        "sequence": sequence,
        "type": event_type,
        "payload": payload,
    }
    return (
        f"event: {event_type}\n"
        f"id: {sequence}\n"
        f"data: {json.dumps(envelope, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.IGNORECASE)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _pages(row: dict[str, Any]) -> list[int]:
    for key in ("parent_page_numbers", "page_numbers"):
        value = json_or_value(row.get(key), [])
        if isinstance(value, list) and value:
            pages = []
            for item in value:
                try:
                    pages.append(int(item))
                except (TypeError, ValueError):
                    continue
            if pages:
                return pages
    try:
        return [int(row.get("page_number"))]
    except (TypeError, ValueError):
        return []


def _text_chunks(text: str, size: int) -> list[str]:
    return [text[position:position + size] for position in range(0, len(text), size)]
