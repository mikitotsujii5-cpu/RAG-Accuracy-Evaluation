#!/usr/bin/env python3
"""Verify auditable single-PDF deletion against a deployed Databricks App.

The smoke test creates an isolated two-document Project, builds one Variant,
creates a cited chat answer, logically deletes one PDF, waits for the successor
Variant, and checks the replacement AI Search index. Workspace credentials stay
inside the SDK and HTTP session and are never printed.  Each invocation creates
a fresh disposable Project; resuming an older Project is intentionally not
supported because rebuilding the same configuration would weaken E2E evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import ExecuteStatementRequestOnWaitTimeout

from smoke_test_generic_upload import RefreshingWorkspaceSession, validate_app_base


TERMINAL_PREPARATION_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELED"})
TRACE_ID = re.compile(r"^tr-[0-9a-f]{32}$")


class SmokeFailure(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "新規の使い捨てProjectを作成してPDF削除を検証します。既存Projectの再開は行いません。"
        )
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument("--app-url", required=True)
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--pdf-to-delete", type=Path, required=True)
    parser.add_argument("--pdf-to-keep", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    parser.add_argument(
        "--deleted-document-question",
        default="2024年式プリウスでPDAを一時解除するには何を1回押しますか。",
    )
    parser.add_argument("--deleted-document-answer", default="cancel switch")
    parser.add_argument(
        "--kept-document-question",
        default="2024年式Crown SportのPDA作動速度の上限は何km/hですか。",
    )
    parser.add_argument("--kept-document-answer", default="50")
    parser.add_argument("--cleanup-project", action="store_true")
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    return args


def require_pdf(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_file() or resolved.suffix.lower() != ".pdf":
        raise SmokeFailure(f"PDFが見つかりません: {resolved}")
    if not resolved.read_bytes().startswith(b"%PDF"):
        raise SmokeFailure(f"PDFヘッダーが不正です: {resolved}")
    return resolved


def api_json(
    session: requests.Session,
    method: str,
    url: str,
    *,
    timeout: float = 90,
    status_out: list[int] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    response = session.request(method, url, timeout=timeout, **kwargs)
    if status_out is not None:
        status_out.append(int(response.status_code))
    if not 200 <= response.status_code < 300:
        raise SmokeFailure(
            f"{method} {urlparse(url).path} returned HTTP {response.status_code}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise SmokeFailure(
            f"{method} {urlparse(url).path} did not return JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise SmokeFailure(f"{method} {urlparse(url).path} returned invalid JSON")
    return payload


def upload_document(
    session: requests.Session,
    base: str,
    project_id: str,
    path: Path,
    *,
    title: str,
) -> dict[str, Any]:
    metadata = {
        "title": title,
        "category": "車両デモ資料",
        "tags": ["PDF削除E2E", "監査保持"],
        "source": "RAG精度評価アプリ自動テスト",
    }
    with path.open("rb") as handle:
        return api_json(
            session,
            "POST",
            f"{base}/api/projects/{project_id}/documents",
            timeout=180,
            data={"metadata": json.dumps(metadata, ensure_ascii=False)},
            files={"file": (path.name, handle, "application/pdf")},
        )


def wait_for_documents(
    session: requests.Session,
    base: str,
    project_id: str,
    document_ids: set[str],
    deadline: float,
) -> list[dict[str, Any]]:
    next_heartbeat = 0.0
    while time.monotonic() < deadline:
        payload = api_json(session, "GET", f"{base}/api/projects/{project_id}/documents")
        items = payload.get("items")
        if not isinstance(items, list):
            raise SmokeFailure("documents API returned invalid items")
        selected = [
            item for item in items
            if isinstance(item, dict) and item.get("document_id") in document_ids
        ]
        states = {
            str(item.get("document_id")): str(item.get("processing_status") or "").upper()
            for item in selected
        }
        failed = {key: value for key, value in states.items() if value in {"ERROR", "FAILED"}}
        if failed:
            raise SmokeFailure(f"Document Parsing failed: {failed}")
        if len(selected) == len(document_ids) and all(
            value in {"PARSED", "READY"} for value in states.values()
        ):
            return selected
        now = time.monotonic()
        if now >= next_heartbeat:
            print(f"Document Parsing待機中: {states}", flush=True)
            next_heartbeat = now + 30
        time.sleep(5)
    raise TimeoutError("Document Parsing timed out")


def choose_model(items: Any, capability: str) -> dict[str, Any]:
    if not isinstance(items, list):
        raise SmokeFailure(f"{capability} model-options returned invalid items")
    selectable = [
        item for item in items
        if isinstance(item, dict) and item.get("selectable") is True
    ]
    preferred = next(
        (
            item for item in selectable
            if item.get("is_default") is True
            or (capability == "embedding" and "qwen3" in str(item.get("model_key") or "").lower())
            or (capability == "chat" and item.get("display_name") == "GPT-5.6 Luna")
        ),
        selectable[0] if selectable else None,
    )
    if preferred is None or not preferred.get("model_key"):
        raise SmokeFailure(f"selectable {capability} model is unavailable")
    return preferred


def wait_for_preparation(
    session: requests.Session,
    base: str,
    project_id: str,
    prep_run_id: str,
    deadline: float,
) -> dict[str, Any]:
    next_heartbeat = 0.0
    while time.monotonic() < deadline:
        run = api_json(
            session,
            "GET",
            f"{base}/api/projects/{project_id}/preparation-runs/{prep_run_id}",
        )
        status = str(run.get("status") or "UNKNOWN").upper()
        if status in TERMINAL_PREPARATION_STATUSES:
            if status != "SUCCEEDED":
                raise SmokeFailure(
                    f"Preparation {prep_run_id} ended as {status}: "
                    f"{run.get('error_message') or ''}"
                )
            return run
        now = time.monotonic()
        if now >= next_heartbeat:
            print(
                "Variant待機中: "
                f"run={prep_run_id} status={status} step={run.get('current_step')} "
                f"job={run.get('job_state') or 'UNKNOWN'}",
                flush=True,
            )
            next_heartbeat = now + 30
        retry_ms = run.get("retry_after_ms")
        try:
            delay = max(5.0, min(30.0, float(retry_ms) / 1000))
        except (TypeError, ValueError):
            delay = 10.0
        time.sleep(delay)
    raise TimeoutError(f"Preparation {prep_run_id} timed out")


def run_chat(
    session: requests.Session,
    base: str,
    project_id: str,
    variant_id: str,
    model_key: str,
    *,
    question: str,
) -> dict[str, Any]:
    chat_session = api_json(
        session,
        "POST",
        f"{base}/api/projects/{project_id}/chat/sessions",
        json={"title": "PDF削除E2E"},
    )
    session_id = str(chat_session["session_id"])
    client_request_id = str(uuid.uuid4())
    response = session.post(
        f"{base}/api/projects/{project_id}/chat/sessions/{session_id}/messages:stream",
        json={
            "message": question,
            "rag_mode": "DETERMINISTIC",
            "retrieval": {"mode": "PRESET", "phase_id": "phase_05"},
            "variant_id": variant_id,
            "answer_model_key": model_key,
            "client_request_id": client_request_id,
        },
        stream=True,
        timeout=(45, 240),
    )
    if not 200 <= response.status_code < 300:
        raise SmokeFailure(f"chat stream returned HTTP {response.status_code}")
    if response.headers.get("X-Request-ID") != client_request_id:
        raise SmokeFailure("chat stream did not preserve client_request_id")
    events: list[dict[str, Any]] = []
    for line in response.iter_lines(decode_unicode=True):
        if line and line.startswith("data:"):
            event = json.loads(line[5:].strip())
            if isinstance(event, dict):
                events.append(event)
    errors = [event for event in events if event.get("type") == "run.error"]
    if errors:
        raise SmokeFailure("chat stream returned run.error")
    answer = "".join(
        str(event.get("payload", {}).get("text") or "")
        for event in events
        if event.get("type") == "response.delta"
    )
    citations = [
        event.get("payload", {}) for event in events
        if event.get("type") == "citation.added"
    ]
    trace_ids = [
        str(event.get("payload", {}).get("trace_id") or "")
        for event in events
        if event.get("type") == "trace.available"
    ]
    if not answer or len(trace_ids) != 1 or not TRACE_ID.fullmatch(trace_ids[0]):
        raise SmokeFailure("chat answer or MLflow Trace is missing")
    history = api_json(
        session,
        "GET",
        f"{base}/api/projects/{project_id}/chat/sessions/{session_id}",
    )
    return {
        "session_id": session_id,
        "request_id": client_request_id,
        "answer": answer,
        "citations": citations,
        "trace_id": trace_ids[0],
        "history": history,
    }


def require_pdf_response(
    session: requests.Session,
    url: str,
) -> None:
    response = session.get(url, timeout=90)
    if (
        response.status_code != 200
        or response.headers.get("content-type") != "application/pdf"
        or not response.content.startswith(b"%PDF")
    ):
        raise SmokeFailure(
            f"GET {urlparse(url).path} did not return the retained PDF"
        )


def sql_rows(
    workspace: WorkspaceClient,
    warehouse_id: str,
    statement: str,
) -> list[list[Any]]:
    response = workspace.statement_execution.execute_statement(
        statement=statement,
        warehouse_id=warehouse_id,
        wait_timeout="50s",
        on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CONTINUE,
    )
    payload = response.as_dict()
    state = str(payload.get("status", {}).get("state") or "")
    if state != "SUCCEEDED":
        raise SmokeFailure(f"SQL verification did not succeed: {state}")
    rows = payload.get("result", {}).get("data_array", [])
    if not isinstance(rows, list):
        raise SmokeFailure("SQL verification returned invalid rows")
    return rows


def main() -> int:
    args = parse_args()
    pdf_to_delete = require_pdf(args.pdf_to_delete)
    pdf_to_keep = require_pdf(args.pdf_to_keep)
    if hashlib.sha256(pdf_to_delete.read_bytes()).digest() == hashlib.sha256(
        pdf_to_keep.read_bytes()
    ).digest():
        raise SmokeFailure("2つの異なるPDFを指定してください")

    workspace = WorkspaceClient(profile=args.profile)
    base = validate_app_base(args.app_url, str(workspace.config.host or ""))
    deadline = time.monotonic() + args.timeout_seconds
    session = RefreshingWorkspaceSession(workspace.config.authenticate)
    project_id: str | None = None
    cleanup_result: dict[str, Any] | None = None
    try:
        health = api_json(session, "GET", f"{base}/api/health")
        if health.get("status") != "ok" or health.get("databricks_ready") is not True:
            raise SmokeFailure("deployed App is not ready")

        suffix = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        project = api_json(
            session,
            "POST",
            f"{base}/api/projects",
            json={
                "name": f"PDF単体削除E2E-{suffix}",
                "description": "2文書から1文書を論理削除し後継Variantを検証",
            },
        )
        project_id = str(project["project_id"])
        uuid.UUID(project_id)
        print(f"使い捨てProjectを作成: {project_id}", flush=True)

        deleted_doc = upload_document(
            session,
            base,
            project_id,
            pdf_to_delete,
            title="削除対象 Prius 2024 安全支援操作ガイド",
        )
        kept_doc = upload_document(
            session,
            base,
            project_id,
            pdf_to_keep,
            title="保持対象 Crown Sport 2024 取扱ガイド",
        )
        deleted_document_id = str(deleted_doc["document_id"])
        kept_document_id = str(kept_doc["document_id"])
        wait_for_documents(
            session,
            base,
            project_id,
            {deleted_document_id, kept_document_id},
            deadline,
        )
        print("2件のDocument Parsingを確認", flush=True)

        embedding = choose_model(
            api_json(session, "GET", f"{base}/api/model-options?capability=embedding").get("items"),
            "embedding",
        )
        chat_model = choose_model(
            api_json(session, "GET", f"{base}/api/model-options?capability=chat").get("items"),
            "chat",
        )
        prep = api_json(
            session,
            "POST",
            f"{base}/api/projects/{project_id}/preparation-runs",
            json={
                "run_type": "BUILD_VARIANT",
                "document_ids": [deleted_document_id, kept_document_id],
                "configuration": {
                    "chunk_method": "STANDARD",
                    "chunk_size_tokens": 256,
                    "parent_chunk_size_tokens": None,
                    "content_profile": "LAYOUT_PRESERVING",
                    "cleaning_enabled": True,
                    "semantic_metadata_enabled": True,
                    "embedding_model_key": embedding["model_key"],
                },
            },
        )
        initial_run = wait_for_preparation(
            session, base, project_id, str(prep["prep_run_id"]), deadline
        )
        initial_prep_run_id = str(prep["prep_run_id"])
        initial_job_run_id = initial_run.get("job_run_id")
        if not initial_job_run_id:
            raise SmokeFailure("initial preparation Job run ID is missing")
        old_variant_id = str(
            initial_run.get("target_variant_id") or prep.get("target_variant_id") or ""
        )
        uuid.UUID(old_variant_id)
        print(f"初期Variant READY: {old_variant_id}", flush=True)

        prior_chat = run_chat(
            session,
            base,
            project_id,
            old_variant_id,
            str(chat_model["model_key"]),
            question=args.deleted_document_question,
        )
        if args.deleted_document_answer.lower() not in prior_chat["answer"].lower():
            raise SmokeFailure("削除前回答が期待文字列を含みません")
        prior_citation_document_ids = {
            str(item.get("document_id") or "") for item in prior_chat["citations"]
        }
        if deleted_document_id not in prior_citation_document_ids:
            raise SmokeFailure("削除前回答が削除対象PDFを引用していません")
        retained_pdf_url = (
            f"{base}/api/projects/{project_id}/documents/{deleted_document_id}/content"
        )
        require_pdf_response(session, retained_pdf_url)
        print("削除前の回答・PDF引用・MLflow Traceを確認", flush=True)

        initial_delete_http_status: list[int] = []
        deletion = api_json(
            session,
            "DELETE",
            f"{base}/api/projects/{project_id}/documents/{deleted_document_id}",
            timeout=180,
            status_out=initial_delete_http_status,
        )
        if initial_delete_http_status != [202]:
            raise SmokeFailure("initial DELETE did not return HTTP 202")
        if (
            deletion.get("deleted") is not True
            or deletion.get("deletion_mode") != "LOGICAL"
            or deletion.get("physical_file_retained") is not True
        ):
            raise SmokeFailure("DELETE response did not confirm auditable logical deletion")
        rebuilds = deletion.get("rebuilds")
        if not isinstance(rebuilds, list) or not rebuilds:
            raise SmokeFailure("DELETE response did not contain a successor rebuild")

        listed_documents = api_json(
            session, "GET", f"{base}/api/projects/{project_id}/documents"
        ).get("items", [])
        listed_ids = {
            str(item.get("document_id") or "")
            for item in listed_documents
            if isinstance(item, dict)
            and str(item.get("lifecycle_status") or "ACTIVE").upper() != "DELETING"
        }
        if deleted_document_id in listed_ids or kept_document_id not in listed_ids:
            raise SmokeFailure("document catalog did not hide only the deleted PDF")

        # The old answer and its authenticated PDF route remain usable even
        # though the document is no longer a retrieval source.
        saved_history = api_json(
            session,
            "GET",
            f"{base}/api/projects/{project_id}/chat/sessions/{prior_chat['session_id']}",
        )
        if deleted_document_id not in json.dumps(saved_history, ensure_ascii=False):
            raise SmokeFailure("past chat citation was not retained")
        require_pdf_response(session, retained_pdf_url)

        replay = api_json(
            session,
            "DELETE",
            f"{base}/api/projects/{project_id}/documents/{deleted_document_id}",
            timeout=180,
        )
        if replay.get("already_deleted") is not True:
            raise SmokeFailure("repeated DELETE was not idempotent")
        print("PDF非表示・過去引用保持・DELETE冪等性を確認", flush=True)

        completed_rebuilds: list[dict[str, Any]] = []
        for rebuild in rebuilds:
            if not isinstance(rebuild, dict) or not rebuild.get("preparation_run_id"):
                raise SmokeFailure("rebuild item is invalid")
            completed_run = wait_for_preparation(
                session,
                base,
                project_id,
                str(rebuild["preparation_run_id"]),
                deadline,
            )
            if not completed_run.get("job_run_id"):
                raise SmokeFailure("successor preparation Job run ID is missing")
            completed_rebuilds.append({
                **rebuild,
                "job_run_id": completed_run["job_run_id"],
                "terminal_status": completed_run.get("status"),
            })
        active_rebuild = next(
            (
                item for item in completed_rebuilds
                if item.get("activate_on_success") is True
            ),
            completed_rebuilds[0],
        )
        replacement_variant_id = str(active_rebuild["replacement_variant_id"])
        successor_prep_run_id = str(active_rebuild["preparation_run_id"])
        successor_job_run_id = str(active_rebuild["job_run_id"])

        projects = api_json(session, "GET", f"{base}/api/projects").get("items", [])
        stored_project = next(
            (
                item for item in projects
                if isinstance(item, dict) and item.get("project_id") == project_id
            ),
            None,
        )
        if not stored_project or stored_project.get("active_variant_id") != replacement_variant_id:
            raise SmokeFailure("successor Variant did not become the active Variant")
        variants = api_json(
            session, "GET", f"{base}/api/projects/{project_id}/variants"
        ).get("items", [])
        ready_ids = {
            str(item.get("variant_id") or "")
            for item in variants
            if isinstance(item, dict) and str(item.get("status") or "").upper() == "READY"
        }
        if old_variant_id in ready_ids or replacement_variant_id not in ready_ids:
            raise SmokeFailure("old/successor Variant visibility is incorrect")

        escaped_project = project_id.replace("'", "''")
        escaped_variant = replacement_variant_id.replace("'", "''")
        escaped_old_variant = old_variant_id.replace("'", "''")
        escaped_deleted = deleted_document_id.replace("'", "''")
        escaped_kept = kept_document_id.replace("'", "''")
        old_variant_rows = sql_rows(
            workspace,
            args.warehouse_id,
            "SELECT lifecycle_status, superseded_by_variant_id, superseded_reason FROM "
            "mikito_toyota_rag_eval.rag_accuracy.toyota_index_variants "
            f"WHERE project_id='{escaped_project}' AND variant_id='{escaped_old_variant}' "
            "LIMIT 1",
        )
        if len(old_variant_rows) != 1 or len(old_variant_rows[0]) < 3:
            raise SmokeFailure("old Variant registry row is missing")
        old_variant_state = {
            "lifecycle_status": str(old_variant_rows[0][0] or "").upper(),
            "superseded_by_variant_id": str(old_variant_rows[0][1] or ""),
            "superseded_reason": str(old_variant_rows[0][2] or ""),
        }
        expected_superseded_reason = f"DOCUMENT_DELETED:{deleted_document_id}"
        if old_variant_state != {
            "lifecycle_status": "SUPERSEDED",
            "superseded_by_variant_id": replacement_variant_id,
            "superseded_reason": expected_superseded_reason,
        }:
            raise SmokeFailure("old Variant did not retain the expected supersession lineage")

        registry_rows = sql_rows(
            workspace,
            args.warehouse_id,
            "SELECT lifecycle_status, processing_status, deletion_request_id, "
            "CAST(deleted_at AS STRING) FROM "
            "mikito_toyota_rag_eval.rag_accuracy.toyota_document_registry "
            f"WHERE project_id='{escaped_project}' AND document_id='{escaped_deleted}' "
            "LIMIT 1",
        )
        if len(registry_rows) != 1 or len(registry_rows[0]) < 4:
            raise SmokeFailure("deleted document registry row is missing")
        document_registry_state = {
            "lifecycle_status": str(registry_rows[0][0] or "").upper(),
            "processing_status": str(registry_rows[0][1] or "").upper(),
            "deletion_request_id": str(registry_rows[0][2] or ""),
            "deleted_at": str(registry_rows[0][3] or ""),
        }
        if (
            document_registry_state["lifecycle_status"] != "DELETED"
            or document_registry_state["processing_status"] != "DELETED"
            or not document_registry_state["deletion_request_id"]
            or not document_registry_state["deleted_at"]
        ):
            raise SmokeFailure("document registry deletion tombstone is incomplete")
        try:
            uuid.UUID(document_registry_state["deletion_request_id"])
        except ValueError as exc:
            raise SmokeFailure("document registry deletion_request_id is not a UUID") from exc

        rows = sql_rows(
            workspace,
            args.warehouse_id,
            "SELECT index_name, source_table FROM "
            "mikito_toyota_rag_eval.rag_accuracy.toyota_index_variants "
            f"WHERE project_id='{escaped_project}' AND variant_id='{escaped_variant}' "
            "AND lifecycle_status='READY' LIMIT 1",
        )
        if len(rows) != 1 or len(rows[0]) < 2:
            raise SmokeFailure("successor Variant registry row is missing")
        index_name, source_table = str(rows[0][0]), str(rows[0][1])
        source_rows = sql_rows(
            workspace,
            args.warehouse_id,
            "SELECT count(*), "
            f"count_if(document_id='{escaped_deleted}'), "
            f"count_if(document_id='{escaped_kept}') FROM {source_table}",
        )
        if not source_rows or len(source_rows[0]) < 3:
            raise SmokeFailure("successor Delta source counts are missing")
        source_total_rows = int(source_rows[0][0])
        deleted_source_rows = int(source_rows[0][1])
        retained_source_rows = int(source_rows[0][2])
        if (
            source_total_rows <= 0
            or deleted_source_rows != 0
            or retained_source_rows <= 0
        ):
            raise SmokeFailure("successor Delta source document membership is incorrect")

        index = workspace.vector_search_indexes.get_index(
            index_name=index_name
        ).as_dict()
        indexed_row_count = int(index.get("status", {}).get("indexed_row_count") or 0)
        if indexed_row_count != source_total_rows:
            raise SmokeFailure(
                "successor AI Search indexed_row_count does not match its Delta source"
            )

        search = workspace.vector_search_indexes.query_index(
            index_name=index_name,
            columns=["document_id", "title", "doc_uri"],
            query_text=args.deleted_document_question,
            query_type="HYBRID",
            num_results=20,
        ).as_dict()
        manifest_columns = [
            str(item.get("name") or "")
            for item in search.get("manifest", {}).get("columns", [])
            if isinstance(item, dict)
        ]
        try:
            document_position = manifest_columns.index("document_id")
        except ValueError as exc:
            raise SmokeFailure("AI Search response omitted document_id") from exc
        search_document_ids = {
            str(row[document_position])
            for row in search.get("result", {}).get("data_array", [])
            if isinstance(row, list) and len(row) > document_position
        }
        if deleted_document_id in search_document_ids:
            raise SmokeFailure("successor AI Search index returned the deleted document")

        retained_search = workspace.vector_search_indexes.query_index(
            index_name=index_name,
            columns=["document_id", "title", "doc_uri"],
            query_text=args.kept_document_question,
            query_type="HYBRID",
            num_results=20,
        ).as_dict()
        retained_manifest_columns = [
            str(item.get("name") or "")
            for item in retained_search.get("manifest", {}).get("columns", [])
            if isinstance(item, dict)
        ]
        try:
            retained_document_position = retained_manifest_columns.index("document_id")
        except ValueError as exc:
            raise SmokeFailure("retained-document AI Search response omitted document_id") from exc
        retained_search_document_ids = {
            str(row[retained_document_position])
            for row in retained_search.get("result", {}).get("data_array", [])
            if isinstance(row, list) and len(row) > retained_document_position
        }
        if kept_document_id not in retained_search_document_ids:
            raise SmokeFailure("successor AI Search index did not return the retained document")

        kept_chat = run_chat(
            session,
            base,
            project_id,
            replacement_variant_id,
            str(chat_model["model_key"]),
            question=args.kept_document_question,
        )
        if args.kept_document_answer.lower() not in kept_chat["answer"].lower():
            raise SmokeFailure("successor Variant answer did not contain the expected value")
        if kept_document_id not in {
            str(item.get("document_id") or "") for item in kept_chat["citations"]
        }:
            raise SmokeFailure("successor Variant answer did not cite the retained PDF")
        if deleted_document_id in {
            str(item.get("document_id") or "") for item in kept_chat["citations"]
        }:
            raise SmokeFailure("successor Variant answer cited the deleted PDF")
        print("後継Delta Table・AI Search・回答引用を確認", flush=True)

        if args.cleanup_project:
            cleanup_result = api_json(
                session, "DELETE", f"{base}/api/projects/{project_id}", timeout=180
            )
            if cleanup_result.get("deleted") is not True:
                raise SmokeFailure("test Project cleanup did not succeed")

        print(json.dumps({
            "status": "SUCCEEDED",
            "project_id": project_id,
            "deleted_document_id": deleted_document_id,
            "retained_document_id": kept_document_id,
            "old_variant_id": old_variant_id,
            "replacement_variant_id": replacement_variant_id,
            "initial_prep_run_id": initial_prep_run_id,
            "initial_job_run_id": str(initial_job_run_id),
            "initial_delete_http_status": initial_delete_http_status[0],
            "successor_prep_run_id": successor_prep_run_id,
            "successor_job_run_id": successor_job_run_id,
            "successor_preparation_runs": completed_rebuilds,
            "old_variant_state": old_variant_state,
            "document_registry_state": document_registry_state,
            "replacement_index_name": index_name,
            "replacement_source_rows": source_total_rows,
            "replacement_indexed_rows": indexed_row_count,
            "deleted_source_rows": deleted_source_rows,
            "retained_source_rows": retained_source_rows,
            "ai_search_deleted_hits": int(deleted_document_id in search_document_ids),
            "ai_search_retained_hits": int(
                kept_document_id in retained_search_document_ids
            ),
            "past_citation_retained": True,
            "delete_replay_idempotent": True,
            "prior_session_id": prior_chat["session_id"],
            "prior_request_id": prior_chat["request_id"],
            "prior_trace_id": prior_chat["trace_id"],
            "successor_session_id": kept_chat["session_id"],
            "successor_request_id": kept_chat["request_id"],
            "successor_trace_id": kept_chat["trace_id"],
            "project_cleanup": bool(cleanup_result and cleanup_result.get("deleted")),
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
