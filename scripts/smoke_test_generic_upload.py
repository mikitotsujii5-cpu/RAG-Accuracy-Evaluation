#!/usr/bin/env python3
"""Create a non-vehicle Project and verify generic PDF ingestion end to end.

The short-lived Workspace credential stays inside the HTTP session and is
never printed.  The created Project and document are intentionally retained so
the preparation, chat, and evaluation smoke tests can reuse their IDs.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from databricks.sdk import WorkspaceClient


TERMINAL_DOCUMENT_STATES = frozenset({"PARSED", "READY", "FAILED"})


class ApiStatusError(RuntimeError):
    """HTTP failure that never includes credentials, response bodies, or query text."""

    def __init__(self, method: str, url: str, status_code: int) -> None:
        super().__init__(f"{method} {urlparse(url).path} returned HTTP {status_code}")
        self.status_code = status_code


class RefreshingWorkspaceSession(requests.Session):
    """Keep the short-lived token private and refresh idempotent reads once."""

    def __init__(self, authenticate: Callable[[], dict[str, str]]) -> None:
        super().__init__()
        self._authenticate = authenticate
        self.refresh_authentication()

    def refresh_authentication(self) -> None:
        headers = self._authenticate()
        authorization = headers.get("Authorization") if isinstance(headers, dict) else None
        if not isinstance(authorization, str) or not authorization:
            raise RuntimeError("The selected profile did not provide short-lived authentication")
        self.headers["Authorization"] = authorization
        headers.clear()

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        response = super().request(method, url, **kwargs)
        # Never replay a Project/document-creating POST. Long-running polling
        # uses GET, which is safe to retry once with a refreshed token.
        if response.status_code not in {401, 403} or method.upper() not in {"GET", "HEAD"}:
            return response
        response.close()
        self.refresh_authentication()
        return super().request(method, url, **kwargs)

    def close(self) -> None:
        self.headers.pop("Authorization", None)
        super().close()


def validate_app_base(app_url: str, workspace_host: str) -> str:
    """Reject any origin that could receive a Workspace token unexpectedly."""

    target = urlparse(app_url.strip())
    workspace = urlparse(workspace_host.strip())
    try:
        port = target.port
    except ValueError as exc:
        raise RuntimeError("--app-url contains an invalid port") from exc
    if (
        target.scheme != "https"
        or not target.hostname
        or not target.hostname.lower().endswith(".databricksapps.com")
        or target.username is not None
        or target.password is not None
        or port not in {None, 443}
        or target.path not in {"", "/"}
        or target.params
        or target.query
        or target.fragment
    ):
        raise RuntimeError("--app-url must be a bare HTTPS Databricks Apps origin")
    if workspace.scheme != "https" or not workspace.hostname:
        raise RuntimeError("The selected profile does not contain a valid HTTPS Workspace host")
    workspace_ids = set(re.findall(r"(?<!\d)\d{10,}(?!\d)", workspace.hostname or ""))
    if workspace_ids:
        app_workspace_id = re.search(r"-(\d{10,})$", target.hostname.split(".", 1)[0])
        if not app_workspace_id or app_workspace_id.group(1) not in workspace_ids:
            raise RuntimeError("--app-url does not belong to the selected Workspace")
    return f"https://{target.hostname.lower()}" + (":443" if port == 443 else "")


def request_json(
    session: requests.Session,
    method: str,
    url: str,
    *,
    timeout: float = 60,
    **kwargs: Any,
) -> dict[str, Any]:
    response = session.request(method, url, timeout=timeout, **kwargs)
    if not 200 <= response.status_code < 300:
        raise ApiStatusError(method, url, response.status_code)
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{method} {urlparse(url).path} did not return JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"{method} {urlparse(url).path} returned a non-object JSON value"
        )
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--app-url", required=True)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--project-name")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pdf_path = args.pdf.resolve()
    if not pdf_path.is_file() or pdf_path.suffix.lower() != ".pdf":
        raise RuntimeError("--pdf must point to an existing PDF")
    if args.timeout_seconds <= 0:
        raise RuntimeError("--timeout-seconds must be positive")

    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    project_name = args.project_name or (
        f"情報セキュリティ規程RAG-{timestamp}-{uuid.uuid4().hex[:8]}"
    )
    workspace = WorkspaceClient(profile=args.profile)
    base = validate_app_base(args.app_url, str(workspace.config.host or ""))
    session = RefreshingWorkspaceSession(workspace.config.authenticate)

    health = request_json(session, "GET", f"{base}/api/health")
    if health.get("status") != "ok" or health.get("databricks_ready") is not True:
        raise RuntimeError("The deployed App is not ready")

    project = request_json(
        session,
        "POST",
        f"{base}/api/projects",
        json={
            "name": project_name,
            "description": "車種に依存しない情報セキュリティ規程PDFのRAG検証",
        },
    )
    project_id = project.get("project_id")
    if not isinstance(project_id, str) or not re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        project_id,
    ):
        raise RuntimeError("The Project response did not contain a UUIDv4 project_id")

    metadata = {
        # Title and all Toyota compatibility fields are deliberately omitted.
        "category": "社内規程",
        "tags": ["情報セキュリティ", "インシデント対応"],
        "document_date": "2026-04-01",
        "source": "情報システム部",
        "custom_metadata": {"版": "第3版", "機密区分": "社内公開"},
    }
    with pdf_path.open("rb") as pdf_file:
        document = request_json(
            session,
            "POST",
            f"{base}/api/projects/{project_id}/documents",
            timeout=180,
            data={"metadata": json.dumps(metadata, ensure_ascii=False)},
            files={"file": (pdf_path.name, pdf_file, "application/pdf")},
        )
    document_id = document.get("document_id")
    try:
        parsed_document_id = uuid.UUID(str(document_id))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("The upload response did not contain a UUIDv4 document_id") from exc
    if parsed_document_id.version != 4 or str(parsed_document_id) != document_id:
        raise RuntimeError("The upload response did not contain a UUIDv4 document_id")

    deadline = time.monotonic() + args.timeout_seconds
    stored: dict[str, Any] | None = None
    last_state = ""
    while time.monotonic() < deadline:
        items = request_json(
            session,
            "GET",
            f"{base}/api/projects/{project_id}/documents",
        ).get("items")
        if not isinstance(items, list):
            raise RuntimeError("The documents endpoint returned invalid items")
        stored = next(
            (
                item
                for item in items
                if isinstance(item, dict) and item.get("document_id") == document_id
            ),
            None,
        )
        if stored is None:
            raise RuntimeError("The uploaded document was not returned by the Project")
        last_state = str(stored.get("processing_status") or "").upper()
        if last_state in TERMINAL_DOCUMENT_STATES:
            break
        time.sleep(8)
    if stored is None or last_state not in {"PARSED", "READY"}:
        message = stored.get("processing_message") if stored else None
        raise RuntimeError(f"Document Parsing did not succeed: {last_state} {message}")

    expected_title = (
        re.sub(r"\s+", " ", pdf_path.stem.replace("_", " ")).strip() or "文書"
    )[:300]
    expected_fields = {
        "title": expected_title,
        "category": metadata["category"],
        "tags": metadata["tags"],
        "document_date": metadata["document_date"],
        "source": metadata["source"],
        "custom_metadata": metadata["custom_metadata"],
    }
    mismatches = {
        key: {"expected": expected, "actual": stored.get(key)}
        for key, expected in expected_fields.items()
        if stored.get(key) != expected
    }
    legacy_values = {
        key: stored.get(key)
        for key in ("model", "model_year", "document_type", "vehicle_category")
        if stored.get(key) is not None
    }
    if mismatches or legacy_values:
        raise RuntimeError(
            "Generic metadata verification failed: "
            + json.dumps(
                {"mismatches": mismatches, "unexpected_legacy": legacy_values},
                ensure_ascii=False,
            )
        )

    page_count = stored.get("page_count")
    if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count <= 0:
        raise RuntimeError("Document Parsing did not persist a positive page_count")

    session.close()
    print(
        json.dumps(
            {
                "status": "SUCCEEDED",
                "project_id": project_id,
                "project_name": project_name,
                "document_id": document_id,
                "parse_run_id": document.get("parse_run_id"),
                "processing_status": last_state,
                "page_count": page_count,
                "title_auto_generated": True,
                "generic_metadata_verified": True,
                "legacy_vehicle_metadata_absent": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
