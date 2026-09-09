#!/usr/bin/env python3
"""Safely verify deletion of the last logical-ACTIVE PDF in an E2E Project.

Run this companion only after ``smoke_test_document_deletion.py`` has completed
successfully without ``--cleanup-project``.  The default mode is read-only and
performs preflight checks.  Mutation requires both ``--execute-delete`` and an
exact repeated Project ID via ``--confirm-project-id``.
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import ExecuteStatementRequestOnWaitTimeout

from smoke_test_document_deletion import (
    SmokeFailure,
    api_json,
    require_pdf_response,
)
from smoke_test_generic_upload import RefreshingWorkspaceSession, validate_app_base


UC_PREFIX = "rag_accuracy_demo.rag_accuracy"
PROJECTS_TABLE = f"{UC_PREFIX}.toyota_rag_projects"
DOCUMENTS_TABLE = f"{UC_PREFIX}.toyota_document_registry"
VARIANTS_TABLE = f"{UC_PREFIX}.toyota_index_variants"
PREP_RUNS_TABLE = f"{UC_PREFIX}.toyota_rag_prep_runs"
CHAT_RUNS_TABLE = f"{UC_PREFIX}.toyota_rag_chat_runs"
CHAT_MESSAGES_TABLE = f"{UC_PREFIX}.toyota_rag_chat_messages"
EVAL_RUNS_TABLE = f"{UC_PREFIX}.toyota_rag_eval_runs"
PARSED_TABLE = f"{UC_PREFIX}.toyota_parsed_v2"
DISPOSABLE_PROJECT_PREFIX = "PDF単体削除E2E-"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--app-url", required=True)
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--final-document-id", required=True)
    parser.add_argument("--previously-deleted-document-id", required=True)
    parser.add_argument("--expected-active-variant-id", required=True)
    parser.add_argument(
        "--execute-delete",
        action="store_true",
        help="Preflight後に最後のPDFを論理削除する",
    )
    parser.add_argument(
        "--confirm-project-id",
        help="--execute-delete時に--project-idと同じ値を再入力する",
    )
    parser.add_argument("--sql-timeout-seconds", type=int, default=180)
    args = parser.parse_args()
    for field in (
        "project_id",
        "final_document_id",
        "previously_deleted_document_id",
        "expected_active_variant_id",
    ):
        try:
            setattr(args, field, str(uuid.UUID(str(getattr(args, field)))))
        except ValueError as exc:
            parser.error(f"--{field.replace('_', '-')} must be a UUID: {exc}")
    if args.final_document_id == args.previously_deleted_document_id:
        parser.error("the two document IDs must be different")
    if args.sql_timeout_seconds <= 0:
        parser.error("--sql-timeout-seconds must be positive")
    if args.execute_delete:
        try:
            confirmation = str(uuid.UUID(str(args.confirm_project_id)))
        except (TypeError, ValueError) as exc:
            parser.error(f"--confirm-project-id is required and must be a UUID: {exc}")
        if confirmation != args.project_id:
            parser.error("--confirm-project-id must exactly match --project-id")
    return args


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    method = getattr(value, "as_dict", None)
    if callable(method):
        result = method()
        if isinstance(result, dict):
            return result
    raise SmokeFailure("Databricks SQL returned an invalid response")


def statement_state(payload: dict[str, Any]) -> str:
    return str(payload.get("status", {}).get("state") or "UNKNOWN").upper()


def sql_rows(
    workspace: WorkspaceClient,
    warehouse_id: str,
    statement: str,
    *,
    timeout_seconds: int,
) -> list[list[Any]]:
    response = workspace.statement_execution.execute_statement(
        statement=statement,
        warehouse_id=warehouse_id,
        wait_timeout="50s",
        on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CONTINUE,
    )
    payload = as_dict(response)
    statement_id = str(payload.get("statement_id") or "")
    deadline = time.monotonic() + timeout_seconds
    while statement_state(payload) in {"PENDING", "RUNNING"}:
        if not statement_id or time.monotonic() >= deadline:
            if statement_id:
                try:
                    workspace.statement_execution.cancel_execution(statement_id)
                except Exception:
                    pass
            raise SmokeFailure("SQL verification timed out")
        time.sleep(1)
        payload = as_dict(
            workspace.statement_execution.get_statement(statement_id)
        )
    if statement_state(payload) != "SUCCEEDED":
        raise SmokeFailure(
            f"SQL verification ended as {statement_state(payload)}"
        )
    rows = payload.get("result", {}).get("data_array", [])
    if not isinstance(rows, list):
        raise SmokeFailure("SQL verification returned invalid rows")
    return rows


def exactly_one(rows: list[list[Any]], label: str) -> list[Any]:
    if len(rows) != 1 or not isinstance(rows[0], list):
        raise SmokeFailure(f"{label} did not return exactly one row")
    return rows[0]


def scalar_int(rows: list[list[Any]], label: str) -> int:
    row = exactly_one(rows, label)
    if len(row) != 1:
        raise SmokeFailure(f"{label} did not return one column")
    try:
        return int(row[0])
    except (TypeError, ValueError) as exc:
        raise SmokeFailure(f"{label} was not an integer") from exc


def project_item(
    session: RefreshingWorkspaceSession,
    base: str,
    project_id: str,
) -> dict[str, Any]:
    items = api_json(session, "GET", f"{base}/api/projects").get("items")
    if not isinstance(items, list):
        raise SmokeFailure("projects API returned invalid items")
    selected = [
        item for item in items
        if isinstance(item, dict) and item.get("project_id") == project_id
    ]
    if len(selected) != 1:
        raise SmokeFailure("target Project is not uniquely visible to this user")
    return selected[0]


def citation_session_id(
    workspace: WorkspaceClient,
    warehouse_id: str,
    project_id: str,
    document_id: str,
    *,
    timeout_seconds: int,
) -> str:
    rows = sql_rows(
        workspace,
        warehouse_id,
        f"""SELECT session_id FROM {CHAT_MESSAGES_TABLE}
        WHERE project_id={sql_literal(project_id)} AND role='assistant'
          AND exists(citations, item -> item.document_id={sql_literal(document_id)})
        ORDER BY created_at DESC LIMIT 1""",
        timeout_seconds=timeout_seconds,
    )
    row = exactly_one(rows, f"citation session for {document_id}")
    try:
        return str(uuid.UUID(str(row[0])))
    except (IndexError, ValueError) as exc:
        raise SmokeFailure("citation session ID is invalid") from exc


def require_history_citation(
    session: RefreshingWorkspaceSession,
    base: str,
    project_id: str,
    session_id: str,
    document_id: str,
) -> None:
    history = api_json(
        session,
        "GET",
        f"{base}/api/projects/{project_id}/chat/sessions/{session_id}",
    )
    if document_id not in json.dumps(history, ensure_ascii=False):
        raise SmokeFailure(f"past citation for {document_id} is missing")


def active_run_count(
    workspace: WorkspaceClient,
    warehouse_id: str,
    project_id: str,
    *,
    timeout_seconds: int,
) -> int:
    project = sql_literal(project_id)
    return scalar_int(
        sql_rows(
            workspace,
            warehouse_id,
            f"""SELECT
              (SELECT count(*) FROM {PREP_RUNS_TABLE}
               WHERE project_id={project} AND status IN
                 ('QUEUED','PENDING','PREPARING','RUNNING','TERMINATING','CANCEL_REQUESTED'))
              + (SELECT count(*) FROM {CHAT_RUNS_TABLE}
                 WHERE project_id={project} AND status IN
                   ('QUEUED','STREAMING','CANCEL_REQUESTED'))
              + (SELECT count(*) FROM {EVAL_RUNS_TABLE}
                 WHERE project_id={project} AND status IN
                   ('QUEUED','RUNNING','PARTIAL','CANCEL_REQUESTED')) AS active_runs""",
            timeout_seconds=timeout_seconds,
        ),
        "active run count",
    )


def validate_delete_response(
    payload: dict[str, Any],
    *,
    final_document_id: str,
    expected_variant_id: str,
) -> None:
    impacted = payload.get("impacted_variant_ids")
    if (
        payload.get("document_id") != final_document_id
        or payload.get("deleted") is not True
        or payload.get("deletion_mode") != "LOGICAL"
        or payload.get("physical_file_retained") is not True
        or payload.get("corpus_empty") is not True
        or payload.get("remaining_document_count") != 0
        or payload.get("active_variant_id") is not None
        or payload.get("rebuilds") != []
        or not isinstance(impacted, list)
        or expected_variant_id not in impacted
    ):
        raise SmokeFailure("last-document DELETE response is inconsistent")


def require_delete_http_accepted(statuses: list[int]) -> int:
    """Require the first mutating DELETE to use the asynchronous HTTP contract."""

    if statuses != [202]:
        raise SmokeFailure("initial last-document DELETE did not return HTTP 202")
    return statuses[0]


def main() -> int:
    args = parse_args()
    workspace = WorkspaceClient(profile=args.profile)
    base = validate_app_base(args.app_url, str(workspace.config.host or ""))
    session = RefreshingWorkspaceSession(workspace.config.authenticate)
    project_id = args.project_id
    final_document_id = args.final_document_id
    previous_document_id = args.previously_deleted_document_id
    expected_variant_id = args.expected_active_variant_id
    timeout = args.sql_timeout_seconds
    try:
        health = api_json(session, "GET", f"{base}/api/health")
        if health.get("status") != "ok" or health.get("databricks_ready") is not True:
            raise SmokeFailure("deployed App is not ready")

        project = project_item(session, base, project_id)
        if not str(project.get("name") or "").startswith(DISPOSABLE_PROJECT_PREFIX):
            raise SmokeFailure("refusing to mutate a non-disposable E2E Project")
        if str(project.get("role") or "").upper() not in {"OWNER", "EDITOR"}:
            raise SmokeFailure("current user cannot delete PDFs in this Project")
        if (
            str(project.get("status") or "").upper() != "ACTIVE"
            or project.get("active_variant_id") != expected_variant_id
        ):
            raise SmokeFailure("Project is not at the expected post-successor state")

        documents = api_json(
            session, "GET", f"{base}/api/projects/{project_id}/documents"
        ).get("items")
        if not isinstance(documents, list) or len(documents) != 1:
            raise SmokeFailure("preflight requires exactly one visible PDF")
        final_document = documents[0]
        if (
            not isinstance(final_document, dict)
            or final_document.get("document_id") != final_document_id
            or str(final_document.get("lifecycle_status") or "ACTIVE").upper()
            != "ACTIVE"
            or str(final_document.get("processing_status") or "").upper()
            not in {"PARSED", "READY"}
        ):
            raise SmokeFailure("the final visible PDF is not the expected ACTIVE document")

        variants = api_json(
            session, "GET", f"{base}/api/projects/{project_id}/variants"
        ).get("items")
        ready_variant_ids = {
            str(item.get("variant_id") or "")
            for item in variants or []
            if isinstance(item, dict)
            and str(item.get("status") or "READY").upper() == "READY"
        }
        if ready_variant_ids != {expected_variant_id}:
            raise SmokeFailure("preflight requires exactly the expected READY Variant")
        if active_run_count(
            workspace, args.warehouse_id, project_id, timeout_seconds=timeout
        ) != 0:
            raise SmokeFailure("preflight found an active preparation/chat/evaluation run")

        registry_rows = sql_rows(
            workspace,
            args.warehouse_id,
            f"""SELECT document_id, coalesce(lifecycle_status,'ACTIVE')
            FROM {DOCUMENTS_TABLE} WHERE project_id={sql_literal(project_id)}
            ORDER BY document_id""",
            timeout_seconds=timeout,
        )
        registry_state = {str(row[0]): str(row[1]).upper() for row in registry_rows}
        if registry_state != {
            previous_document_id: "DELETED",
            final_document_id: "ACTIVE",
        }:
            raise SmokeFailure("registry does not contain exactly one deleted and one active PDF")

        prep_count_before = scalar_int(
            sql_rows(
                workspace,
                args.warehouse_id,
                f"SELECT count(*) FROM {PREP_RUNS_TABLE} "
                f"WHERE project_id={sql_literal(project_id)}",
                timeout_seconds=timeout,
            ),
            "pre-delete preparation count",
        )
        citation_sessions = {
            document_id: citation_session_id(
                workspace,
                args.warehouse_id,
                project_id,
                document_id,
                timeout_seconds=timeout,
            )
            for document_id in (previous_document_id, final_document_id)
        }
        for document_id, chat_session_id in citation_sessions.items():
            require_pdf_response(
                session,
                f"{base}/api/projects/{project_id}/documents/{document_id}/content",
            )
            require_history_citation(
                session,
                base,
                project_id,
                chat_session_id,
                document_id,
            )

        if not args.execute_delete:
            print(json.dumps({
                "status": "PREFLIGHT_SUCCEEDED",
                "project_id": project_id,
                "final_document_id": final_document_id,
                "expected_active_variant_id": expected_variant_id,
                "prep_run_count": prep_count_before,
                "active_run_count": 0,
                "citation_session_ids": citation_sessions,
                "next_step": (
                    "Re-run with --execute-delete and "
                    f"--confirm-project-id {project_id}"
                ),
            }, ensure_ascii=False, indent=2))
            return 0

        delete_http_statuses: list[int] = []
        deletion = api_json(
            session,
            "DELETE",
            f"{base}/api/projects/{project_id}/documents/{final_document_id}",
            timeout=180,
            status_out=delete_http_statuses,
        )
        delete_http_status = require_delete_http_accepted(delete_http_statuses)
        validate_delete_response(
            deletion,
            final_document_id=final_document_id,
            expected_variant_id=expected_variant_id,
        )
        replay = api_json(
            session,
            "DELETE",
            f"{base}/api/projects/{project_id}/documents/{final_document_id}",
            timeout=180,
        )
        if replay.get("already_deleted") is not True or replay.get("deleted") is not True:
            raise SmokeFailure("last-document DELETE replay was not idempotent")

        if api_json(
            session, "GET", f"{base}/api/projects/{project_id}/documents"
        ).get("items") != []:
            raise SmokeFailure("document catalog is not empty")
        if api_json(
            session, "GET", f"{base}/api/projects/{project_id}/variants"
        ).get("items") != []:
            raise SmokeFailure("READY Variant list is not empty")
        empty_project = project_item(session, base, project_id)
        if (
            str(empty_project.get("status") or "").upper() != "EMPTY"
            or empty_project.get("active_variant_id") is not None
        ):
            raise SmokeFailure("Project did not converge to EMPTY with no active Variant")

        project_row = exactly_one(
            sql_rows(
                workspace,
                args.warehouse_id,
                f"SELECT status, active_variant_id, mutation_token FROM {PROJECTS_TABLE} "
                f"WHERE project_id={sql_literal(project_id)}",
                timeout_seconds=timeout,
            ),
            "Project final state",
        )
        if str(project_row[0]).upper() != "EMPTY" or project_row[1] is not None or project_row[2] is not None:
            raise SmokeFailure("Project SQL state or mutation fence is incorrect")

        document_counts = exactly_one(
            sql_rows(
                workspace,
                args.warehouse_id,
                f"""SELECT
                  count_if(coalesce(lifecycle_status,'ACTIVE')='ACTIVE'),
                  count_if(lifecycle_status='DELETING'),
                  count_if(lifecycle_status='DELETED')
                FROM {DOCUMENTS_TABLE} WHERE project_id={sql_literal(project_id)}""",
                timeout_seconds=timeout,
            ),
            "document lifecycle counts",
        )
        if [int(value) for value in document_counts] != [0, 0, 2]:
            raise SmokeFailure("document lifecycle did not converge to two DELETED rows")

        ready_variant_count = scalar_int(
            sql_rows(
                workspace,
                args.warehouse_id,
                f"SELECT count_if(coalesce(lifecycle_status,'READY')='READY') "
                f"FROM {VARIANTS_TABLE} WHERE project_id={sql_literal(project_id)}",
                timeout_seconds=timeout,
            ),
            "READY Variant count",
        )
        if ready_variant_count != 0:
            raise SmokeFailure("a READY Variant remained after deleting the last PDF")
        final_variant = exactly_one(
            sql_rows(
                workspace,
                args.warehouse_id,
                f"""SELECT lifecycle_status, superseded_by_variant_id, superseded_reason
                FROM {VARIANTS_TABLE}
                WHERE project_id={sql_literal(project_id)}
                  AND variant_id={sql_literal(expected_variant_id)}""",
                timeout_seconds=timeout,
            ),
            "final Variant lifecycle",
        )
        expected_reason = f"DOCUMENT_DELETED:{final_document_id}"
        if (
            str(final_variant[0]).upper() != "SUPERSEDED"
            or final_variant[1] is not None
            or str(final_variant[2]) != expected_reason
        ):
            raise SmokeFailure("final Variant was not superseded without an empty successor")

        prep_count_after = scalar_int(
            sql_rows(
                workspace,
                args.warehouse_id,
                f"SELECT count(*) FROM {PREP_RUNS_TABLE} "
                f"WHERE project_id={sql_literal(project_id)}",
                timeout_seconds=timeout,
            ),
            "post-delete preparation count",
        )
        if prep_count_after != prep_count_before:
            raise SmokeFailure("last-PDF deletion unexpectedly created a preparation run")
        parsed_count = scalar_int(
            sql_rows(
                workspace,
                args.warehouse_id,
                f"SELECT count(DISTINCT document_id) FROM {PARSED_TABLE} "
                f"WHERE project_id={sql_literal(project_id)}",
                timeout_seconds=timeout,
            ),
            "retained parsed-document count",
        )
        if parsed_count != 2:
            raise SmokeFailure("parsed audit rows were not retained")

        for document_id, chat_session_id in citation_sessions.items():
            require_pdf_response(
                session,
                f"{base}/api/projects/{project_id}/documents/{document_id}/content",
            )
            require_history_citation(
                session,
                base,
                project_id,
                chat_session_id,
                document_id,
            )

        print(json.dumps({
            "status": "SUCCEEDED",
            "project_id": project_id,
            "project_status": "EMPTY",
            "active_variant_id": None,
            "deleted_document_count": 2,
            "ready_variant_count": ready_variant_count,
            "prep_run_count_before": prep_count_before,
            "prep_run_count_after": prep_count_after,
            "delete_http_status": delete_http_status,
            "delete_replay_idempotent": True,
            "mutation_released": True,
            "pdf_originals_retained": True,
            "parsed_rows_retained": parsed_count,
            "past_citations_retained": True,
            "citation_session_ids": citation_sessions,
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
