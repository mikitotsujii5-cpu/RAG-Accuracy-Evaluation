# Databricks notebook source
# MAGIC %run ./job_common

# COMMAND ----------

"""Run the immutable Phase 1-5 offline evaluation batch.

Only ``eval_run_id`` crosses the Job boundary.  Every other setting is read
from Delta, hash-verified, and resolved against server-side allow-lists.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.serving import ChatMessage
from pyspark.sql import functions as F
from pyspark.sql import types as T


dbutils.widgets.text("eval_run_id", "", "Evaluation run ID")
EVAL_RUN_ID = require_uuid(dbutils.widgets.get("eval_run_id").strip(), "eval_run_id")
workspace = WorkspaceClient()


RETRIEVED_ITEM_TYPE = T.StructType(
    [
        T.StructField("rank", T.IntegerType()),
        T.StructField("chunk_id", T.StringType()),
        T.StructField("document_id", T.StringType()),
        T.StructField("doc_uri", T.StringType()),
        T.StructField("page_numbers", T.ArrayType(T.IntegerType())),
        T.StructField("matched_child_page_numbers", T.ArrayType(T.IntegerType())),
        T.StructField("parent_page_numbers", T.ArrayType(T.IntegerType())),
        T.StructField("score", T.DoubleType()),
    ]
)
CITATION_TYPE = T.StructType(
    [
        T.StructField("citation_id", T.StringType()),
        T.StructField("document_id", T.StringType()),
        T.StructField("doc_uri", T.StringType()),
        T.StructField("page_numbers", T.ArrayType(T.IntegerType())),
    ]
)
RESULT_SCHEMA = T.StructType(
    [
        T.StructField("project_id", T.StringType()),
        T.StructField("eval_run_id", T.StringType()),
        T.StructField("mlflow_run_id", T.StringType()),
        T.StructField("eval_case_id", T.StringType()),
        T.StructField("trial_no", T.IntegerType()),
        T.StructField("trace_id", T.StringType()),
        T.StructField("performance_trace_id", T.StringType()),
        T.StructField("dataset_version", T.StringType()),
        T.StructField("dataset_split", T.StringType()),
        T.StructField("phase_id", T.StringType()),
        T.StructField("variant_id", T.StringType()),
        T.StructField("config_hash", T.StringType()),
        T.StructField("query_type", T.StringType()),
        T.StructField("metadata_filtering", T.BooleanType()),
        T.StructField("reranking", T.BooleanType()),
        T.StructField("query_optimization", T.BooleanType()),
        T.StructField("embedding_model_key", T.StringType()),
        T.StructField("answer_model_key", T.StringType()),
        T.StructField("answer_model_target_kind", T.StringType()),
        T.StructField("answer_model_target_name", T.StringType()),
        T.StructField("prompt_version", T.StringType()),
        T.StructField("validated_filter_json", T.StringType()),
        T.StructField("expanded_queries", T.ArrayType(T.StringType())),
        T.StructField("answer", T.StringType()),
        T.StructField("retrieved_items", T.ArrayType(RETRIEVED_ITEM_TYPE)),
        T.StructField("citations", T.ArrayType(CITATION_TYPE)),
        T.StructField("retrieval_page_recall_at_10_pages", T.DoubleType()),
        T.StructField("retrieval_page_precision_at_10_pages", T.DoubleType()),
        T.StructField("retrieval_page_dcg_at_10_pages", T.DoubleType()),
        T.StructField("retrieval_page_ndcg_at_10_pages", T.DoubleType()),
        T.StructField("page_coverage_recall_in_top_10_chunks", T.DoubleType()),
        T.StructField("answer_correctness", T.BooleanType()),
        T.StructField("groundedness", T.BooleanType()),
        T.StructField("citation_correctness", T.BooleanType()),
        T.StructField("assessment_rationales", T.MapType(T.StringType(), T.StringType())),
        T.StructField("filter_exact_match", T.BooleanType()),
        T.StructField("filter_extraction_ms", T.DoubleType()),
        T.StructField("query_expansion_ms", T.DoubleType()),
        T.StructField("retrieval_ms", T.DoubleType()),
        T.StructField("reranker_ms", T.DoubleType()),
        T.StructField("reranker_status", T.StringType()),
        T.StructField("reranker_warnings_json", T.StringType()),
        T.StructField("server_ttft_ms", T.DoubleType()),
        T.StructField("client_ttft_ms", T.DoubleType()),
        T.StructField("e2e_latency_ms", T.DoubleType()),
        T.StructField("input_tokens", T.LongType()),
        T.StructField("output_tokens", T.LongType()),
        T.StructField("total_tokens", T.LongType()),
        T.StructField("is_error", T.BooleanType()),
        T.StructField("error_code", T.StringType()),
        T.StructField("evaluated_at", T.TimestampType()),
    ]
)
SUGGESTION_SCHEMA = T.StructType(
    [
        T.StructField("suggestion_id", T.StringType()),
        T.StructField("project_id", T.StringType()),
        T.StructField("eval_run_id", T.StringType()),
        T.StructField("phase_id", T.StringType()),
        T.StructField("advisor_model_key", T.StringType()),
        T.StructField("advisor_prompt_version", T.StringType()),
        T.StructField("suggestion_schema_version", T.StringType()),
        T.StructField("evidence_json", T.StringType()),
        T.StructField("suggestion_json", T.StringType()),
        T.StructField("confidence", T.DoubleType()),
        T.StructField("created_at", T.TimestampType()),
        T.StructField("accepted_by", T.StringType()),
        T.StructField("accepted_at", T.TimestampType()),
    ]
)


def persist_result(payload: dict[str, Any]) -> None:
    frame = spark.createDataFrame([payload], schema=RESULT_SCHEMA)
    frame.createOrReplaceTempView("_toyota_eval_result_source")
    spark.sql(
        f"""
        MERGE INTO {EVAL_RESULTS_TABLE} AS target
        USING _toyota_eval_result_source AS source
        ON target.project_id=source.project_id
         AND target.eval_run_id=source.eval_run_id
         AND target.phase_id=source.phase_id
         AND target.eval_case_id=source.eval_case_id
         AND target.trial_no=source.trial_no
        WHEN MATCHED THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *
        """
    )


def persist_suggestion(payload: dict[str, Any]) -> None:
    frame = spark.createDataFrame([payload], schema=SUGGESTION_SCHEMA)
    frame.createOrReplaceTempView("_toyota_eval_suggestion_source")
    spark.sql(
        f"""
        MERGE INTO {EVAL_SUGGESTIONS_TABLE} AS target
        USING _toyota_eval_suggestion_source AS source
        ON target.project_id=source.project_id
         AND target.eval_run_id=source.eval_run_id
         AND target.phase_id=source.phase_id
         AND target.suggestion_id=source.suggestion_id
        WHEN MATCHED THEN UPDATE SET
          target.advisor_model_key=source.advisor_model_key,
          target.advisor_prompt_version=source.advisor_prompt_version,
          target.suggestion_schema_version=source.suggestion_schema_version,
          target.evidence_json=source.evidence_json,
          target.suggestion_json=source.suggestion_json,
          target.confidence=source.confidence,
          target.created_at=source.created_at
        WHEN NOT MATCHED THEN INSERT *
        """
    )


def update_batch(assignments: str, phase_id: str | None = None) -> None:
    phase_clause = "" if phase_id is None else f" AND phase_id={sql_string(phase_id)}"
    spark.sql(
        f"UPDATE {EVAL_RUNS_TABLE} SET {assignments} "
        f"WHERE eval_run_id={sql_string(EVAL_RUN_ID)}{phase_clause}"
    )


def invoke_model(endpoint_name: str, messages: list[dict[str, str]], max_tokens: int) -> tuple[str, dict[str, Any]]:
    # Use the public SDK surface and explicit ChatMessage models.  This maps to
    # the same OpenAI-compatible FMAPI payload used by app/gateway.py.
    endpoint_path(endpoint_name)  # Validate the server-side catalog value.
    raw = workspace.serving_endpoints.query(
        name=endpoint_name,
        messages=[ChatMessage.from_dict(message) for message in messages],
        max_tokens=max_tokens,
    )
    return parse_model_text(deep_dict(raw))


class SearchGateway:
    """Small compatibility boundary for the current AI Search Python client."""

    def __init__(
        self,
        index_name: str,
        search_endpoint: str = BASELINE_SEARCH_ENDPOINT,
    ) -> None:
        self.index_name = index_name
        self.search_endpoint = search_endpoint
        try:
            from databricks.ai_search.client import AISearchClient
        except ImportError as exc:
            raise RuntimeError(
                "databricks-ai-search is required; verify the Job library installation"
            ) from exc
        client = AISearchClient()
        try:
            self.index = client.get_index(index_name=index_name)
        except TypeError:
            self.index = client.get_index(
                endpoint_name=search_endpoint,
                index_name=index_name,
            )

    def query(
        self,
        *,
        query_text: str,
        query_type: str,
        filters: dict[str, Any],
        reranking: bool,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], float]:
        # In databricks-ai-search 0.78, DatabricksReranker automatically
        # overfetches 50 candidates; num_results remains the final top-k.
        candidate_count = 10
        kwargs: dict[str, Any] = {
            "query_text": query_text,
            "query_type": query_type,
            "columns": RETURN_COLUMNS,
            "num_results": candidate_count,
            "debug_level": 1,
        }
        if filters:
            kwargs["filters"] = filters
        if reranking:
            from databricks.ai_search.reranker import DatabricksReranker

            kwargs["reranker"] = DatabricksReranker(
                columns_to_rerank=["title", "chunk_to_retrieve"]
            )
        started = time.perf_counter()
        try:
            raw = self.index.similarity_search(**kwargs)
        except TypeError:
            # Older compatible clients may not expose debug_level.
            kwargs.pop("debug_level", None)
            raw = self.index.similarity_search(**kwargs)
        elapsed_ms = (time.perf_counter() - started) * 1000
        rows, debug = parse_search_response(deep_dict(raw))
        return rows, debug, elapsed_ms


def resolve_model(model_rows: dict[str, dict[str, Any]], model_key: str, capability: str) -> dict[str, Any]:
    model = model_rows.get(model_key)
    if model is None or not bool(model.get("selectable")) or not bool(model.get("region_available")):
        raise JobContractError(f"model {model_key} is not selectable")
    capabilities = set(model.get("capabilities") or [])
    if capability == "judge":
        valid = "judge" in capabilities or "chat" in capabilities
    elif capability == "advisor":
        valid = "advisor" in capabilities or "chat" in capabilities
    else:
        valid = capability in capabilities
    if not valid or model.get("target_kind") != "FMAPI_ENDPOINT":
        raise JobContractError(f"model {model_key} does not satisfy {capability}")
    endpoint_path(str(model["target_name"]))
    return model


def expand_queries(question: str, endpoint_name: str) -> tuple[list[str], float, dict[str, Any], str | None]:
    started = time.perf_counter()
    try:
        text, usage = invoke_model(
            endpoint_name,
            [
                {
                    "role": "system",
                    "content": "あなたは日本語の検索クエリ最適化担当です。JSONだけを返します。",
                },
                {
                    "role": "user",
                    "content": (
                        "質問中の固有名詞、型番、専門用語、日付を保ったまま、AI Search向けの異なる"
                        "検索クエリを最大2件作成してください。"
                        '\n形式: {"queries":["...","..."]}\n質問: ' + question
                    ),
                },
            ],
            220,
        )
        parsed = extract_json_object(text)
        candidates = parsed.get("queries") if isinstance(parsed.get("queries"), list) else []
        extras = [
            str(item).strip()[:1000]
            for item in candidates
            if isinstance(item, str) and item.strip()
        ][:2]
        queries = list(dict.fromkeys([question, *extras]))[:3]
        return queries, (time.perf_counter() - started) * 1000, usage, None
    except Exception:
        return [question], (time.perf_counter() - started) * 1000, {}, "QUERY_EXPANSION_FAILED"


def retrieve(
    search: SearchGateway,
    *,
    queries: list[str],
    phase: dict[str, Any],
    filters: dict[str, Any],
    expected_project_id: str,
    expected_variant_id: str,
) -> tuple[list[dict[str, Any]], float, float | None, list[str]]:
    required_scope = merge_project_variant_filters(
        project_id=expected_project_id,
        variant_id=expected_variant_id,
    )
    if any(filters.get(key) != value for key, value in required_scope.items()):
        raise JobContractError(
            "every AI Search query must include the selected Project and Variant"
        )
    per_query: list[list[dict[str, Any]]] = []
    retrieval_ms = 0.0
    reranker_measurements: list[float] = []
    warnings: list[str] = []
    for query in queries:
        rows, debug, elapsed = search.query(
            query_text=query,
            query_type=phase["query_type"],
            filters=filters,
            reranking=bool(phase["reranking"]),
        )
        retrieval_ms += elapsed
        if phase["reranking"]:
            measured = extract_reranker_ms(debug)
            if measured is not None:
                reranker_measurements.append(measured)
            else:
                warnings.append("reranker latency is included in retrieval_ms")
        debug_warnings = debug.get("warnings") if isinstance(debug, dict) else None
        if isinstance(debug_warnings, str):
            warnings.append(debug_warnings)
        elif isinstance(debug_warnings, list):
            warnings.extend(str(item) for item in debug_warnings)
        per_query.append(rows)

    # Every candidate list is first reranked by DatabricksReranker in Phase
    # 4/5.  Phase 5 then performs a global reciprocal-rank fusion after
    # deduplication, so all expanded queries contribute to the final ordering.
    fused_by_chunk: dict[str, dict[str, Any]] = {}
    fusion_scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    ordinal = 0
    for rows in per_query:
        for position, row in enumerate(rows, start=1):
            if str(row.get("project_id")) != expected_project_id:
                raise JobContractError("AI Search returned a row from another Project")
            if str(row.get("variant_id")) != expected_variant_id:
                raise JobContractError("AI Search returned a row from another Variant")
            chunk_id = str(row.get("chunk_id") or "")
            if not chunk_id:
                continue
            if chunk_id not in fused_by_chunk:
                fused_by_chunk[chunk_id] = dict(row)
                first_seen[chunk_id] = ordinal
                ordinal += 1
            fusion_scores[chunk_id] = fusion_scores.get(chunk_id, 0.0) + 1.0 / (60 + position)
    fused: list[dict[str, Any]] = []
    for chunk_id in sorted(
        fused_by_chunk,
        key=lambda key: (-fusion_scores[key], first_seen[key]),
    ):
        row = fused_by_chunk[chunk_id]
        row["score"] = fusion_scores[chunk_id]
        fused.append(row)

    # Parent-child variants return at most one context per parent.
    final: list[dict[str, Any]] = []
    seen_parents: set[str] = set()
    for row in fused:
        key = str(row.get("parent_chunk_id") or row["chunk_id"])
        if key in seen_parents:
            continue
        seen_parents.add(key)
        final.append(row)
        if len(final) == 10:
            break
    reranker_ms = sum(reranker_measurements) if reranker_measurements else None
    return final, retrieval_ms, reranker_ms, list(dict.fromkeys(warnings))


def answer_question(
    question: str,
    retrieved_rows: list[dict[str, Any]],
    endpoint_name: str,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    if not retrieved_rows:
        return "根拠を確認できませんでした。", [], {}
    sources: list[str] = []
    source_citations: list[dict[str, Any]] = []
    for rank, row in enumerate(retrieved_rows, start=1):
        pages = row_pages(row) or [1]
        citation_id = f"S{rank}:p{pages[0]}"
        context = row.get("parent_chunk_to_retrieve") or row.get("chunk_to_retrieve") or ""
        sources.append(
            f"[{citation_id}] {row.get('title') or '文書'} / page {pages[0]}\n{str(context)[:7000]}"
        )
        source_citations.append(
            {
                "citation_id": citation_id,
                "document_id": str(row["document_id"]),
                "doc_uri": str(row["doc_uri"]),
                "page_numbers": pages,
            }
        )
    answer, usage = invoke_model(
        endpoint_name,
        [
            {
                "role": "system",
                "content": "あなたは任意分野のPDF資料に対応するRAG回答アシスタントです。根拠のない推測は禁止です。",
            },
            {
                "role": "user",
                "content": (
                    "次の検索結果だけを根拠に日本語で回答してください。重要な事実の直後に"
                    "必ず [S1:p3] 形式の引用IDを付け、資料にない場合は回答できないと明記"
                    f"してください。\n\n質問:\n{question}\n\n検索結果:\n"
                    + "\n\n".join(sources)
                ),
            },
        ],
        1100,
    )
    valid = {item["citation_id"]: item for item in source_citations}
    mentioned = set(re.findall(r"\[(S\d+:p\d+)\]", answer))
    invalid = mentioned - set(valid)
    for citation_id in invalid:
        answer = answer.replace(f"[{citation_id}]", "")
    cited = [item for item in source_citations if item["citation_id"] in mentioned]
    if not cited:
        cited = [source_citations[0]]
        answer = answer.rstrip() + f"\n\n根拠: [{cited[0]['citation_id']}]"
    return answer, cited, usage


def judge_answer(
    *,
    question: str,
    expected_answer: str | None,
    answer: str,
    citations: list[dict[str, Any]],
    retrieved_rows: list[dict[str, Any]],
    endpoint_name: str,
) -> tuple[bool | None, bool | None, bool | None, dict[str, str], dict[str, Any]]:
    evidence = []
    for rank, row in enumerate(retrieved_rows, start=1):
        evidence.append(
            f"S{rank}: {str(row.get('parent_chunk_to_retrieve') or row.get('chunk_to_retrieve') or '')[:3500]}"
        )
    try:
        text, usage = invoke_model(
            endpoint_name,
            [
                {
                    "role": "system",
                    "content": "あなたはRAG評価者です。提示された情報だけで判定し、JSONだけを返します。",
                },
                {
                    "role": "user",
                    "content": (
                        "回答の正しさ、検索根拠への忠実性、引用の正しさをtrue/falseで判定"
                        "してください。\n"
                        '{"answer_correctness":true,"groundedness":true,'
                        '"citation_correctness":true,"rationales":'
                        '{"answer_correctness":"...","groundedness":"...",'
                        '"citation_correctness":"..."}}\n'
                        f"質問: {question}\n期待回答: {expected_answer or ''}\n回答: {answer}\n"
                        f"引用: {canonical_json(citations)}\n根拠:\n" + "\n".join(evidence)
                    ),
                },
            ],
            700,
        )
        parsed = extract_json_object(text)
        rationales = parsed.get("rationales") if isinstance(parsed.get("rationales"), dict) else {}
        return (
            parsed.get("answer_correctness") if isinstance(parsed.get("answer_correctness"), bool) else None,
            parsed.get("groundedness") if isinstance(parsed.get("groundedness"), bool) else None,
            parsed.get("citation_correctness") if isinstance(parsed.get("citation_correctness"), bool) else None,
            {str(key): str(value)[:2000] for key, value in rationales.items()},
            usage,
        )
    except Exception:
        return None, None, None, {"judge": "JUDGE_UNAVAILABLE"}, {}


def add_usage(*usage_values: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    inputs = outputs = totals = 0
    has_input = has_output = has_total = False
    for usage in usage_values:
        input_tokens, output_tokens, total_tokens = usage_counts(usage)
        if input_tokens is not None:
            inputs += input_tokens
            has_input = True
        if output_tokens is not None:
            outputs += output_tokens
            has_output = True
        if total_tokens is not None:
            totals += total_tokens
            has_total = True
    return inputs if has_input else None, outputs if has_output else None, totals if has_total else None


def result_base(
    *,
    batch: dict[str, Any],
    phase_id: str,
    case: dict[str, Any],
    trial_no: int,
    phase: dict[str, Any],
    mlflow_run_id: str | None,
    answer_model: dict[str, Any],
    embedding_model_key: str,
    resolved_variant_id: str,
) -> dict[str, Any]:
    return {
        field.name: None for field in RESULT_SCHEMA.fields
    } | {
        "project_id": str(batch["project_id"]),
        "eval_run_id": EVAL_RUN_ID,
        "mlflow_run_id": mlflow_run_id,
        "eval_case_id": str(case["eval_case_id"]),
        "trial_no": trial_no,
        "dataset_version": str(batch["dataset_version"]),
        "dataset_split": str(batch["dataset_split"]),
        "phase_id": phase_id,
        "variant_id": resolved_variant_id,
        "config_hash": str(batch["config_hash"]),
        "query_type": phase["query_type"],
        "metadata_filtering": bool(phase["metadata_filtering"]),
        "reranking": bool(phase["reranking"]),
        "query_optimization": bool(phase["query_optimization"]),
        "embedding_model_key": embedding_model_key,
        "answer_model_key": str(batch["answer_model_key"]),
        "answer_model_target_kind": str(answer_model["target_kind"]),
        "answer_model_target_name": str(answer_model["target_name"]),
        "prompt_version": "generic-rag-answer-v2",
        "validated_filter_json": "{}",
        "expanded_queries": [str(case["question"])],
        "retrieved_items": [],
        "citations": [],
        "assessment_rationales": {},
        "reranker_status": "NOT_REQUESTED" if not phase["reranking"] else "REQUESTED",
        "reranker_warnings_json": "[]",
        "is_error": False,
        "evaluated_at": datetime.now(UTC),
    }


TRACE_DIMENSION_KEYS = (
    "project_id",
    "eval_run_id",
    "eval_case_id",
    "phase_id",
    "variant_id",
)


def evaluation_trace_dimensions(
    *,
    project_id: str,
    eval_case_id: str,
    phase_id: str,
    variant_id: str,
) -> dict[str, str]:
    """Return the non-secret dimensions shared by one offline quality Trace."""

    return {
        "project_id": project_id,
        "eval_run_id": EVAL_RUN_ID,
        "eval_case_id": eval_case_id,
        "phase_id": phase_id,
        "variant_id": variant_id,
    }


def require_recording_trace_id(root_span: Any) -> str:
    """Fail closed instead of writing a result that points at a no-op Trace."""

    trace_id = str(getattr(root_span, "trace_id", "") or "")
    if not trace_id or trace_id == "MLFLOW_NO_OP_SPAN_TRACE_ID":
        raise RuntimeError("MLflow did not create a recording Trace")
    return trace_id


def retrieval_trace_documents(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Serialize final retrieval rows in MLflow's Document-compatible shape."""

    documents: list[dict[str, Any]] = []
    for rank, row in enumerate(rows, start=1):
        pages = row_pages(row)
        metadata: dict[str, Any] = {
            "rank": rank,
            "project_id": str(row["project_id"]),
            "variant_id": str(row["variant_id"]),
            "document_id": str(row["document_id"]),
            "doc_uri": str(row["doc_uri"]),
            "page_numbers": pages,
        }
        for key in ("title", "model", "model_year", "document_type", "vehicle_category"):
            if row.get(key) is not None:
                metadata[key] = str(row[key])
        content = row.get("parent_chunk_to_retrieve") or row.get("chunk_to_retrieve") or ""
        documents.append(
            Document(
                page_content=str(content),
                metadata=metadata,
                id=str(row["chunk_id"]),
            ).to_dict()
        )
    return documents


def evaluation_trace_output(payload: dict[str, Any]) -> dict[str, Any]:
    """Build a compact output without credentials or model token payloads."""

    return {
        "status": "ERROR" if payload["is_error"] else "OK",
        "answer": payload.get("answer"),
        "citations": payload.get("citations") or [],
        "retrieved_items": payload.get("retrieved_items") or [],
        "quality": {
            "retrieval_page_recall_at_10_pages": payload.get(
                "retrieval_page_recall_at_10_pages"
            ),
            "retrieval_page_precision_at_10_pages": payload.get(
                "retrieval_page_precision_at_10_pages"
            ),
            "retrieval_page_ndcg_at_10_pages": payload.get(
                "retrieval_page_ndcg_at_10_pages"
            ),
            "answer_correctness": payload.get("answer_correctness"),
            "groundedness": payload.get("groundedness"),
            "citation_correctness": payload.get("citation_correctness"),
        },
        "error_code": payload.get("error_code"),
    }


def suggestion_for_phase(
    *,
    project_id: str,
    phase_id: str,
    payloads: list[dict[str, Any]],
    advisor_key: str,
    advisor_endpoint: str,
) -> dict[str, Any]:
    successful = [item for item in payloads if not item["is_error"]]

    def average(field: str) -> float | None:
        values = [float(item[field]) for item in successful if item.get(field) is not None]
        return sum(values) / len(values) if values else None

    worst = sorted(
        successful,
        key=lambda item: (
            item.get("retrieval_page_ndcg_at_10_pages") is not None,
            item.get("retrieval_page_ndcg_at_10_pages") or 0.0,
        ),
    )[:8]
    evidence_case_ids = list(dict.fromkeys(str(item["eval_case_id"]) for item in worst))
    if not evidence_case_ids and payloads:
        evidence_case_ids = [str(payloads[0]["eval_case_id"])]
    evidence = {
        "phase_id": phase_id,
        "trial_count": len(payloads),
        "error_rate": (sum(1 for item in payloads if item["is_error"]) / len(payloads)) if payloads else 1.0,
        "recall_at_10": average("retrieval_page_recall_at_10_pages"),
        "precision_at_10": average("retrieval_page_precision_at_10_pages"),
        "ndcg_at_10": average("retrieval_page_ndcg_at_10_pages"),
        "answer_correctness": average("answer_correctness"),
        "groundedness": average("groundedness"),
        "citation_correctness": average("citation_correctness"),
        "latency_ms": average("e2e_latency_ms"),
        "total_tokens": average("total_tokens"),
        "evidence_case_ids": evidence_case_ids,
        "worst_cases": [
            {
                "eval_case_id": item["eval_case_id"],
                "recall_at_10": item.get("retrieval_page_recall_at_10_pages"),
                "ndcg_at_10": item.get("retrieval_page_ndcg_at_10_pages"),
                "answer_correctness": item.get("answer_correctness"),
                "groundedness": item.get("groundedness"),
                "citation_correctness": item.get("citation_correctness"),
                "assessment_rationales": {
                    str(key): str(value)[:500]
                    for key, value in (item.get("assessment_rationales") or {}).items()
                    if key in {
                        "answer_correctness", "groundedness", "citation_correctness"
                    }
                },
                "is_error": item["is_error"],
                "error_code": item.get("error_code"),
            }
            for item in worst
        ],
    }
    allowed_targets = {
        "retrieval", "metadata", "reranking", "query_optimization",
        "chunking", "embedding", "prompt", "evaluation_data",
    }
    suggestion: dict[str, Any] = {}
    confidence = 0.55
    try:
        text, _ = invoke_model(
            advisor_endpoint,
            [
                {
                    "role": "system",
                    "content": "あなたはRAG精度改善アドバイザーです。JSONだけを返します。",
                },
                {
                    "role": "user",
                    "content": (
                        "次の評価集計を根拠に、次の再評価で実施できる具体的な改善を1件提案"
                        "してください。根拠にない数値を作らないでください。\n"
                        '{"evidence_case_ids":["..."],"diagnosis":"...",'
                        '"priority":"high|medium|low","target":'
                        '"retrieval|metadata|reranking|query_optimization|chunking|embedding|prompt|evaluation_data",'
                        '"proposed_change":"...","expected_effect":"...",'
                        '"tradeoffs":["..."],"retest_plan":"...","confidence":0.0}\n'
                        + canonical_json(evidence)
                    ),
                },
            ],
            900,
        )
        candidate = extract_json_object(text)
        required_strings = ("diagnosis", "priority", "target", "proposed_change", "expected_effect", "retest_plan")
        if all(isinstance(candidate.get(key), str) and candidate[key].strip() for key in required_strings):
            if candidate["priority"] in {"high", "medium", "low"} and candidate["target"] in allowed_targets:
                suggestion = {
                    "evidence_case_ids": [
                        str(item) for item in (candidate.get("evidence_case_ids") or evidence_case_ids)
                    ][:20],
                    "diagnosis": candidate["diagnosis"][:2000],
                    "priority": candidate["priority"],
                    "target": candidate["target"],
                    "proposed_change": candidate["proposed_change"][:2000],
                    "expected_effect": candidate["expected_effect"][:1000],
                    "tradeoffs": [str(item)[:500] for item in (candidate.get("tradeoffs") or [])][:10],
                    "retest_plan": candidate["retest_plan"][:1000],
                    "generation": "LLM",
                }
                raw_confidence = candidate.get("confidence")
                if isinstance(raw_confidence, (int, float)) and not isinstance(raw_confidence, bool):
                    confidence = min(1.0, max(0.0, float(raw_confidence)))
    except Exception:
        suggestion = {}

    if not suggestion:
        next_target = {
            "phase_01": "retrieval",
            "phase_02": "metadata",
            "phase_03": "reranking",
            "phase_04": "query_optimization",
            "phase_05": "chunking",
        }[phase_id]
        suggestion = {
            "evidence_case_ids": evidence_case_ids,
            "diagnosis": "改善提案LLMの構造化応答を取得できなかったため、Phase順序に基づく再試験が必要です。",
            "priority": "medium",
            "target": next_target,
            "proposed_change": "対象手法の設定を1つだけ変更し、同じ固定評価データで再実行してください。",
            "expected_effect": "変更前後の検索精度とレイテンシを同じ条件で比較できます。",
            "tradeoffs": ["改善提案LLMのendpoint状態を確認してください。"],
            "retest_plan": "同じdataset_version、split、trial_countで再評価します。",
            "generation": "DETERMINISTIC_FALLBACK",
        }
        confidence = 0.3

    prompt_version = "toyota-rag-advisor-v1"
    suggestion_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"{EVAL_RUN_ID}:{phase_id}:{prompt_version}")
    )
    return {
        "suggestion_id": suggestion_id,
        "project_id": project_id,
        "eval_run_id": EVAL_RUN_ID,
        "phase_id": phase_id,
        "advisor_model_key": advisor_key,
        "advisor_prompt_version": prompt_version,
        "suggestion_schema_version": "1.0",
        "evidence_json": canonical_json(evidence),
        "suggestion_json": canonical_json(suggestion),
        "confidence": confidence,
        "created_at": datetime.now(UTC),
        "accepted_by": None,
        "accepted_at": None,
    }


# COMMAND ----------

mlflow_module = None
mlflow_created_run = False
mlflow_run_id: str | None = None
success_output: dict[str, Any] | None = None

try:
    run_rows = (
        spark.table(EVAL_RUNS_TABLE)
        .where(F.col("eval_run_id") == EVAL_RUN_ID)
        .orderBy("phase_id")
        .collect()
    )
    batch = validate_eval_batch(run_rows)
    project_id = str(batch["project_id"])
    if any(row["cancel_requested_at"] is not None for row in run_rows):
        update_batch(
            "status='CANCELED', completed_at=current_timestamp(), error_message=NULL"
        )
        success_output = {"eval_run_id": EVAL_RUN_ID, "status": "CANCELED"}
    else:
        selected_case_ids = batch["config"].get("evaluation_case_ids")
        cases_frame = (
            spark.table(EVAL_CASES_TABLE)
            .where(F.col("project_id") == project_id)
            .where(F.col("dataset_version") == batch["dataset_version"])
            .where(F.col("dataset_split") == batch["dataset_split"])
        )
        if selected_case_ids is not None:
            cases_frame = cases_frame.where(F.col("eval_case_id").isin(selected_case_ids))
        raw_cases = [
            deep_dict(row)
            for row in (
                cases_frame
                .orderBy("eval_case_id")
                .collect()
            )
        ]
        if selected_case_ids is not None and {
            str(case.get("eval_case_id") or "") for case in raw_cases
        } != set(selected_case_ids):
            raise JobContractError(
                "selected evaluation cases escaped or no longer match the persisted run"
            )
        expected_case_count = None
        if (
            selected_case_ids is None
            and project_id == BASELINE_PROJECT_ID
            and batch["dataset_version"] == "v1.0.0"
        ):
            expected_case_count = 12 if batch["dataset_split"] == "development" else 4
        cases = validate_eval_cases(
            raw_cases,
            project_id=project_id,
            dataset_version=str(batch["dataset_version"]),
            dataset_split=str(batch["dataset_split"]),
            expected_count=expected_case_count,
        )
        project_doc_uris = {
            str(row["doc_uri"])
            for row in (
                spark.table(DOCUMENTS_TABLE)
                .where(F.col("project_id") == project_id)
                .select("doc_uri")
                .distinct()
                .collect()
            )
        }
        validate_eval_case_document_scope(
            cases,
            allowed_doc_uris=project_doc_uris,
        )

        project_rows = (
            spark.table(PROJECTS_TABLE)
            .where(F.col("project_id") == project_id)
            .limit(2)
            .collect()
        )
        if len(project_rows) != 1 or deep_dict(project_rows[0]).get("status") == "ARCHIVED":
            raise JobContractError("evaluation Project is missing, duplicated, or archived")
        project = deep_dict(project_rows[0])

        model_catalog_records = [
            deep_dict(row) for row in spark.table(MODEL_CATALOG_TABLE).collect()
        ]
        model_rows = {
            str(row["model_key"]): row for row in model_catalog_records
        }
        if len(model_rows) != len(model_catalog_records):
            raise JobContractError("model catalog contains duplicate model_key rows")
        answer_model = resolve_model(model_rows, str(batch["answer_model_key"]), "chat")
        judge_model = resolve_model(model_rows, str(batch["judge_model_key"]), "judge")
        optimizer_key = str(batch.get("query_optimizer_model_key") or project.get("query_optimizer_model_key") or batch["answer_model_key"])
        advisor_key = str(batch.get("advisor_model_key") or project.get("advisor_model_key") or batch["judge_model_key"])
        optimizer_model = resolve_model(model_rows, optimizer_key, "chat")
        advisor_model = resolve_model(model_rows, advisor_key, "advisor")

        requested_variant_id = str(batch["variant_id"])
        variant_id = resolve_project_variant_id(
            project_id=project_id,
            requested_variant_id=requested_variant_id,
            active_variant_id=project.get("active_variant_id"),
        )
        variants = (
            spark.table(VARIANTS_TABLE)
            .where(F.col("project_id") == project_id)
            .where(F.col("variant_id") == variant_id)
            .where(F.coalesce(F.col("lifecycle_status"), F.lit("READY")) == "READY")
            .limit(2)
            .collect()
        )
        if len(variants) != 1:
            raise JobContractError("requested ready Index Variant was not found")
        variant = deep_dict(variants[0])
        indexed_variant_id = variant_id
        source_table = str(variant.get("source_table") or "")
        index_name = str(variant.get("index_name") or "")
        validate_variant_resource_pair(source_table, index_name)
        chunker_config = variant.get("chunker_config_json") or "{}"
        if isinstance(chunker_config, str):
            try:
                chunker_config = json.loads(chunker_config)
            except json.JSONDecodeError as exc:
                raise JobContractError("Variant chunker configuration is invalid") from exc
        if not isinstance(chunker_config, dict):
            raise JobContractError("Variant chunker configuration is invalid")
        index_profile_key = str(
            chunker_config.get("index_profile_key")
            or (
                BASELINE_INDEX_PROFILE_KEY
                if indexed_variant_id == BASELINE_PHYSICAL_VARIANT_ID
                else ""
            )
        )
        embedding_model_key = str(variant.get("embedding_model_key") or "")
        variant_configuration = {
            "chunk_method": str(variant.get("chunk_method") or ""),
            "chunk_size_tokens": int(variant.get("chunk_size") or 0),
            "parent_chunk_size_tokens": (
                int(variant["parent_chunk_size"])
                if variant.get("parent_chunk_size") is not None
                else None
            ),
            "content_profile": str(
                chunker_config.get("content_profile") or "LAYOUT_PRESERVING"
            ),
            "cleaning_enabled": variant.get("cleaning_enabled"),
            "semantic_metadata_enabled": variant.get("semantic_metadata_enabled"),
            "embedding_model_key": embedding_model_key,
        }
        index_profile = resolve_index_profile(
            index_profile_key,
            variant_configuration,
        )
        if (
            source_table != index_profile["source_table"]
            or index_name != index_profile["index_name"]
            or variant.get("embedding_endpoint") != index_profile["embedding_endpoint"]
            or variant.get("query_embedding_endpoint")
            not in {None, index_profile["embedding_endpoint"]}
        ):
            raise JobContractError(
                "Variant registry does not match the existing Index profile"
            )
        resolve_model(model_rows, embedding_model_key, "embedding")
        existing_index = deep_dict(
            workspace.vector_search_indexes.get_index(index_name=index_name)
        )
        validate_existing_index(existing_index, profile=index_profile)
        source_chunk_count = (
            spark.table(source_table)
            .where(F.col("project_id") == project_id)
            .where(F.col("variant_id") == indexed_variant_id)
            .count()
        )
        if source_chunk_count <= 0:
            raise JobContractError("selected Variant source contains no chunks")

        variant_document_ids = sorted(
            {
                str(row["document_id"])
                for row in (
                    spark.table(source_table)
                    .where(F.col("project_id") == project_id)
                    .where(F.col("variant_id") == indexed_variant_id)
                    .select("document_id")
                    .distinct()
                    .collect()
                )
            }
        )
        registry_rows = [
            deep_dict(row)
            for row in (
                spark.table(DOCUMENTS_TABLE)
                .where(F.col("project_id") == project_id)
                .where(F.col("document_id").isin(variant_document_ids))
                .select(
                    "project_id",
                    "document_id",
                    "category",
                    "tags",
                    F.col("document_date").cast("string").alias("document_date"),
                    "source",
                    "metadata_json",
                    "model",
                    "model_year",
                    "document_type",
                    "vehicle_category",
                )
                .collect()
            )
        ]
        registry_rows = validate_registry_document_scope(
            project_id=project_id,
            requested_document_ids=variant_document_ids,
            registry_rows=registry_rows,
        )
        vehicle_rows = [deep_dict(row) for row in spark.table(VEHICLE_MASTER_TABLE).where(F.col("active") == True).collect()]
        expected_trials = len(cases) * int(batch["trial_count"])
        update_batch(
            "status='RUNNING', expected_trials=" + str(expected_trials) + ", "
            "started_at=coalesce(started_at,current_timestamp()), completed_at=NULL, "
            "error_message=NULL"
        )

        import mlflow
        from mlflow.entities import Document, SpanType

        mlflow_module = mlflow
        if not MLFLOW_EXPERIMENT_PATH or not MLFLOW_EXPERIMENT_ID:
            raise JobContractError(
                "MLFLOW_EXPERIMENT_PATH and MLFLOW_EXPERIMENT_ID must be "
                "configured on the evaluation Job"
            )
        if not MLFLOW_TRACING_SQL_WAREHOUSE_ID:
            raise JobContractError(
                "MLFLOW_TRACING_SQL_WAREHOUSE_ID must be configured on the "
                "evaluation Job"
            )
        experiment = mlflow.set_experiment(MLFLOW_EXPERIMENT_PATH)
        if str(experiment.experiment_id) != MLFLOW_EXPERIMENT_ID:
            raise JobContractError("resolved MLflow Experiment ID does not match deployment state")
        mlflow.start_run(
            run_name=f"rag-eval-{EVAL_RUN_ID}",
            nested=mlflow.active_run() is not None,
        )
        mlflow_created_run = True
        mlflow_run_id = mlflow.active_run().info.run_id
        mlflow.set_tags(
            {
                "eval_run_id": EVAL_RUN_ID,
                "project_id": project_id,
                "dataset_version": str(batch["dataset_version"]),
                "dataset_split": str(batch["dataset_split"]),
            }
        )

        search = SearchGateway(index_name, str(index_profile["search_endpoint"]))
        phase_counts: dict[str, int] = {}
        for phase_id in batch["phases"]:
            phase = PHASE_PRESETS[phase_id]
            phase_payloads: list[dict[str, Any]] = []
            for case in cases:
                for trial_no in range(1, int(batch["trial_count"]) + 1):
                    cancellation = (
                        spark.table(EVAL_RUNS_TABLE)
                        .where(F.col("eval_run_id") == EVAL_RUN_ID)
                        .where(F.col("cancel_requested_at").isNotNull())
                        .limit(1)
                        .count()
                    )
                    if cancellation:
                        update_batch("status='CANCELED', completed_at=current_timestamp(), error_message=NULL")
                        success_output = {"eval_run_id": EVAL_RUN_ID, "status": "CANCELED"}
                        break

                    trial_started = time.perf_counter()
                    payload = result_base(
                        batch=batch,
                        phase_id=phase_id,
                        case=case,
                        trial_no=trial_no,
                        phase=phase,
                        mlflow_run_id=mlflow_run_id,
                        answer_model=answer_model,
                        embedding_model_key=embedding_model_key,
                        resolved_variant_id=indexed_variant_id,
                    )
                    trace_dimensions = evaluation_trace_dimensions(
                        project_id=project_id,
                        eval_case_id=str(case["eval_case_id"]),
                        phase_id=phase_id,
                        variant_id=indexed_variant_id,
                    )
                    with mlflow.start_span(
                        name="offline_evaluation_case",
                        span_type=SpanType.AGENT,
                        attributes=trace_dimensions,
                    ) as root_span:
                        payload["trace_id"] = require_recording_trace_id(root_span)
                        root_span.set_inputs(
                            {
                                "question": str(case["question"]),
                                "trial_no": trial_no,
                                "dataset_version": str(batch["dataset_version"]),
                                "dataset_split": str(batch["dataset_split"]),
                                "retrieval_config": {
                                    "query_type": str(phase["query_type"]),
                                    "metadata_filtering": bool(phase["metadata_filtering"]),
                                    "reranking": bool(phase["reranking"]),
                                    "query_optimization": bool(phase["query_optimization"]),
                                },
                            }
                        )
                        mlflow.update_current_trace(tags=trace_dimensions)
                        try:
                            filter_started = time.perf_counter()
                            metadata_filters, semantic_filters = (
                                resolve_project_search_filters(
                                    str(case["question"]),
                                    registry_rows,
                                    vehicle_rows,
                                )
                                if phase["metadata_filtering"]
                                else ({}, {})
                            )
                            filters = merge_project_variant_filters(
                                project_id=project_id,
                                variant_id=indexed_variant_id,
                                metadata_filters=metadata_filters,
                            )
                            payload["filter_extraction_ms"] = (
                                time.perf_counter() - filter_started
                            ) * 1000
                            payload["validated_filter_json"] = canonical_json(filters)
                            payload["filter_exact_match"] = (
                                filter_exact_match(
                                    semantic_filters,
                                    case.get("expected_filter"),
                                )
                                if phase["metadata_filtering"] else None
                            )

                            expansion_usage: dict[str, Any] = {}
                            expansion_warning: str | None = None
                            if phase["query_optimization"]:
                                queries, expansion_ms, expansion_usage, expansion_warning = expand_queries(
                                    str(case["question"]), str(optimizer_model["target_name"])
                                )
                                payload["query_expansion_ms"] = expansion_ms
                            else:
                                queries = [str(case["question"])]
                                payload["query_expansion_ms"] = 0.0
                            payload["expanded_queries"] = queries

                            with mlflow.start_span(
                                name="final_retrieval",
                                span_type=SpanType.RETRIEVER,
                                attributes=trace_dimensions,
                            ) as retrieval_span:
                                retrieval_span.set_inputs(
                                    {
                                        "question": str(case["question"]),
                                        "expanded_queries": queries,
                                        "filters": filters,
                                        "semantic_filters": semantic_filters,
                                        "query_type": str(phase["query_type"]),
                                        "reranking": bool(phase["reranking"]),
                                    }
                                )
                                rows, retrieval_ms, reranker_ms, warnings = retrieve(
                                    search,
                                    queries=queries,
                                    phase=phase,
                                    filters=filters,
                                    expected_project_id=project_id,
                                    expected_variant_id=indexed_variant_id,
                                )
                                retrieval_documents = retrieval_trace_documents(rows)
                                retrieval_span.set_outputs(retrieval_documents)
                            if expansion_warning:
                                warnings.append(expansion_warning)
                            payload["retrieval_ms"] = retrieval_ms
                            payload["reranker_ms"] = reranker_ms
                            reranker_failures = [
                                warning for warning in warnings
                                if warning not in {
                                    "reranker latency is included in retrieval_ms",
                                    "QUERY_EXPANSION_FAILED",
                                }
                            ]
                            payload["reranker_status"] = (
                                "FALLBACK" if phase["reranking"] and reranker_failures
                                else "APPLIED" if phase["reranking"]
                                else "NOT_REQUESTED"
                            )
                            payload["reranker_warnings_json"] = canonical_json(
                                list(dict.fromkeys(warnings))
                            )

                            retrieved_items: list[dict[str, Any]] = []
                            for rank, row in enumerate(rows, start=1):
                                score_value = row.get("score")
                                try:
                                    score = float(score_value) if score_value is not None else None
                                except (TypeError, ValueError):
                                    score = None
                                child_pages = _to_int_list(row.get("page_numbers"))
                                parent_pages = _to_int_list(row.get("parent_page_numbers"))
                                pages = parent_pages or child_pages or row_pages(row)
                                retrieved_items.append(
                                    {
                                        "rank": rank,
                                        "chunk_id": str(row["chunk_id"]),
                                        "document_id": str(row["document_id"]),
                                        "doc_uri": str(row["doc_uri"]),
                                        "page_numbers": pages,
                                        "matched_child_page_numbers": child_pages,
                                        "parent_page_numbers": parent_pages,
                                        "score": score,
                                    }
                                )
                            payload["retrieved_items"] = retrieved_items
                            metrics = page_metrics(
                                case.get("relevance_judgments") or [],
                                rows,
                                k=10,
                                exclude=str(case.get("question_type")) == "clarification",
                            )
                            payload["retrieval_page_recall_at_10_pages"] = metrics["recall"]
                            payload["retrieval_page_precision_at_10_pages"] = metrics["precision"]
                            payload["retrieval_page_dcg_at_10_pages"] = metrics["dcg"]
                            payload["retrieval_page_ndcg_at_10_pages"] = metrics["ndcg"]
                            payload["page_coverage_recall_in_top_10_chunks"] = metrics[
                                "chunk_page_coverage_recall"
                            ]

                            with mlflow.start_span(
                                name="answer_generation",
                                span_type=SpanType.CHAT_MODEL,
                                attributes={
                                    **trace_dimensions,
                                    "model_key": str(batch["answer_model_key"]),
                                },
                            ) as answer_span:
                                answer_span.set_inputs(
                                    {
                                        "question": str(case["question"]),
                                        "retrieved_documents": retrieval_documents,
                                    }
                                )
                                answer, citations, answer_usage = answer_question(
                                    str(case["question"]),
                                    rows,
                                    str(answer_model["target_name"]),
                                )
                                answer_span.set_outputs(
                                    {"answer": answer, "citations": citations}
                                )
                            payload["answer"] = answer
                            payload["citations"] = citations

                            with mlflow.start_span(
                                name="answer_judge",
                                span_type=SpanType.EVALUATOR,
                                attributes={
                                    **trace_dimensions,
                                    "model_key": str(batch["judge_model_key"]),
                                },
                            ) as judge_span:
                                judge_span.set_inputs(
                                    {
                                        "question": str(case["question"]),
                                        "expected_answer": case.get("expected_answer"),
                                        "answer": answer,
                                        "citations": citations,
                                        "retrieved_documents": retrieval_documents,
                                    }
                                )
                                (
                                    correctness,
                                    groundedness,
                                    citation_correctness,
                                    rationales,
                                    judge_usage,
                                ) = judge_answer(
                                    question=str(case["question"]),
                                    expected_answer=case.get("expected_answer"),
                                    answer=answer,
                                    citations=citations,
                                    retrieved_rows=rows,
                                    endpoint_name=str(judge_model["target_name"]),
                                )
                                if not evaluation_case_has_answer_reference(case):
                                    correctness = None
                                    rationales = dict(rationales)
                                    rationales["answer_correctness"] = (
                                        "期待回答または期待事実が未設定のため判定対象外です。"
                                    )
                                judge_span.set_outputs(
                                    {
                                        "answer_correctness": correctness,
                                        "groundedness": groundedness,
                                        "citation_correctness": citation_correctness,
                                        "rationales": rationales,
                                    }
                                )
                            payload["answer_correctness"] = correctness
                            payload["groundedness"] = groundedness
                            payload["citation_correctness"] = citation_correctness
                            payload["assessment_rationales"] = rationales
                            input_tokens, output_tokens, total_tokens = add_usage(
                                expansion_usage, answer_usage, judge_usage
                            )
                            payload["input_tokens"] = input_tokens
                            payload["output_tokens"] = output_tokens
                            payload["total_tokens"] = total_tokens
                        except Exception as trial_error:
                            print(
                                "evaluation trial failed",
                                phase_id,
                                case["eval_case_id"],
                                trial_no,
                                type(trial_error).__name__,
                                str(trial_error)[:1000],
                            )
                            payload["is_error"] = True
                            payload["error_code"] = type(trial_error).__name__[:120]
                            payload["assessment_rationales"] = {
                                "error": "評価trialに失敗しました。Lakeflow Jobログを確認してください。"
                            }
                            root_span.set_status("ERROR")
                        payload["e2e_latency_ms"] = (
                            time.perf_counter() - trial_started
                        ) * 1000
                        payload["evaluated_at"] = datetime.now(UTC)
                        root_span.set_outputs(evaluation_trace_output(payload))
                    mlflow.flush_trace_async_logging()
                    persist_result(payload)
                    phase_payloads.append(payload)

                if success_output and success_output.get("status") == "CANCELED":
                    break
            if success_output and success_output.get("status") == "CANCELED":
                break

            completed_trials = (
                spark.table(EVAL_RESULTS_TABLE)
                .where(F.col("project_id") == project_id)
                .where(F.col("eval_run_id") == EVAL_RUN_ID)
                .where(F.col("phase_id") == phase_id)
                .count()
            )
            phase_counts[phase_id] = completed_trials
            if completed_trials != expected_trials:
                raise RuntimeError(
                    f"Phase result verification failed: {phase_id} {completed_trials}/{expected_trials}"
                )
            suggestion = suggestion_for_phase(
                project_id=project_id,
                phase_id=phase_id,
                payloads=phase_payloads,
                advisor_key=advisor_key,
                advisor_endpoint=str(advisor_model["target_name"]),
            )
            persist_suggestion(suggestion)
            if phase_payloads and all(item["is_error"] for item in phase_payloads):
                raise RuntimeError(
                    f"all evaluation trials failed for {phase_id}; inspect the first trial error"
                )
            update_batch(
                f"status='SUCCEEDED', completed_trials={completed_trials}, "
                "completed_at=current_timestamp(), error_message=NULL",
                phase_id,
            )
            if mlflow_module is not None:
                try:
                    successful = [item for item in phase_payloads if not item["is_error"]]
                    ndcg_values = [item["retrieval_page_ndcg_at_10_pages"] for item in successful if item["retrieval_page_ndcg_at_10_pages"] is not None]
                    latency_values = [item["e2e_latency_ms"] for item in successful if item["e2e_latency_ms"] is not None]
                    mlflow_module.log_metrics(
                        {
                            f"{phase_id}.ndcg_at_10": sum(ndcg_values) / len(ndcg_values) if ndcg_values else 0.0,
                            f"{phase_id}.latency_ms": sum(latency_values) / len(latency_values) if latency_values else 0.0,
                            f"{phase_id}.error_rate": sum(1 for item in phase_payloads if item["is_error"]) / len(phase_payloads),
                        }
                    )
                except Exception:
                    pass

        if success_output is None:
            success_output = {
                "eval_run_id": EVAL_RUN_ID,
                "project_id": project_id,
                "status": "SUCCEEDED",
                "variant_id": indexed_variant_id,
                "dataset_version": batch["dataset_version"],
                "dataset_split": batch["dataset_split"],
                "phase_trial_counts": phase_counts,
                "mlflow_run_id": mlflow_run_id,
            }
except Exception as exc:
    if isinstance(exc, JobContractError):
        public_error = str(exc)[:900]
    else:
        public_error = "精度評価Jobに失敗しました。Lakeflow Jobの実行ログを確認してください。"
    try:
        spark.sql(
            f"""
            UPDATE {EVAL_RUNS_TABLE}
            SET status='FAILED', completed_at=current_timestamp(),
                error_message={sql_string(public_error)}
            WHERE eval_run_id={sql_string(EVAL_RUN_ID)}
            """
        )
    finally:
        if mlflow_module is not None and mlflow_created_run:
            try:
                mlflow_module.end_run(status="FAILED")
            except Exception:
                pass
    raise
else:
    if mlflow_module is not None and mlflow_created_run:
        try:
            mlflow_module.end_run(
                status="KILLED"
                if success_output and success_output.get("status") == "CANCELED"
                else "FINISHED"
            )
        except Exception:
            pass

dbutils.notebook.exit(json.dumps(success_output, ensure_ascii=False))
