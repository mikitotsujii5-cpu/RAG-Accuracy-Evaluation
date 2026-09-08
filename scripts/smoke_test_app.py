#!/usr/bin/env python3
"""Run a credential-safe E2E smoke test against the deployed Databricks App.

The Workspace access token stays inside the HTTP session and is never printed.
The test creates one chat session, runs one preset phase, verifies the streamed
answer/citations/Trace, reloads the saved history, and opens one cited PDF.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any
from uuid import uuid4

import requests
from databricks.sdk import WorkspaceClient


TRACE_ID = re.compile(r"^tr-[0-9a-f]{32}$")
CITATION_ID = re.compile(r"\[(S\d+:p\d+)\]")


def request_json(
    session: requests.Session,
    method: str,
    url: str,
    **kwargs: Any,
) -> dict[str, Any]:
    response = session.request(method, url, timeout=45, **kwargs)
    response.raise_for_status()
    return response.json()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--app-url", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument(
        "--variant-id",
        help="Use this READY Variant exactly. Otherwise the Project's active Variant is preferred.",
    )
    parser.add_argument(
        "--expected-answer-substring",
        help="Return a failure exit code unless the final answer contains this text.",
    )
    parser.add_argument("--phase", default="phase_05")
    parser.add_argument(
        "--question",
        default="2024年式プリウスのPDA上限速度は何km/hですか。根拠も示してください。",
    )
    return parser.parse_args(argv)


def find_project_active_variant_id(projects: Any, project_id: str) -> str | None:
    if not isinstance(projects, list):
        raise RuntimeError("The projects endpoint returned an invalid items value")
    project = next(
        (
            item
            for item in projects
            if isinstance(item, dict) and item.get("project_id") == project_id
        ),
        None,
    )
    active_variant_id = project.get("active_variant_id") if project else None
    return active_variant_id if isinstance(active_variant_id, str) and active_variant_id else None


def select_ready_variant(
    variants: Any,
    *,
    requested_variant_id: str | None,
    active_variant_id: str | None,
) -> dict[str, Any]:
    if not isinstance(variants, list):
        raise RuntimeError("The variants endpoint returned an invalid items value")
    ready = [
        item
        for item in variants
        if isinstance(item, dict) and str(item.get("status") or "").upper() == "READY"
    ]
    if requested_variant_id:
        selected = next(
            (item for item in ready if item.get("variant_id") == requested_variant_id),
            None,
        )
        if selected is None:
            raise RuntimeError("The requested Variant was not returned as READY")
        return selected
    if active_variant_id:
        active = next(
            (item for item in ready if item.get("variant_id") == active_variant_id),
            None,
        )
        if active is not None:
            return active
    if ready:
        return ready[0]
    raise RuntimeError("No READY Index Variant was returned")


def expected_answer_outcome(
    answer: str,
    expected_substring: str | None,
) -> tuple[bool | None, str, int]:
    if expected_substring is None:
        return None, "SUCCEEDED", 0
    matched = expected_substring in answer
    return matched, "SUCCEEDED" if matched else "FAILED", 0 if matched else 1


def main() -> int:
    args = parse_args()

    base = args.app_url.rstrip("/")
    workspace = WorkspaceClient(profile=args.profile)
    http = requests.Session()
    http.headers.update(workspace.config.authenticate())

    health = request_json(http, "GET", f"{base}/api/health")
    if health.get("status") != "ok" or not health.get("databricks_ready"):
        raise RuntimeError(f"App health is not ready: {health}")

    variants = request_json(
        http, "GET", f"{base}/api/projects/{args.project_id}/variants"
    ).get("items", [])
    active_variant_id = None
    if not args.variant_id:
        projects = request_json(http, "GET", f"{base}/api/projects").get("items", [])
        active_variant_id = find_project_active_variant_id(projects, args.project_id)
    variant = select_ready_variant(
        variants,
        requested_variant_id=args.variant_id,
        active_variant_id=active_variant_id,
    )

    models = request_json(http, "GET", f"{base}/api/model-options?capability=chat").get(
        "items", []
    )
    model = next(
        (item for item in models if item.get("display_name") == "GPT-5.6 Luna"),
        next((item for item in models if item.get("selectable")), None),
    )
    if not model:
        raise RuntimeError("No selectable chat model was returned")

    chat_session = request_json(
        http,
        "POST",
        f"{base}/api/projects/{args.project_id}/chat/sessions",
        json={"title": "Final deployed App E2E"},
    )
    session_id = chat_session["session_id"]
    client_request_id = str(uuid4())
    payload = {
        "message": args.question,
        "rag_mode": "DETERMINISTIC",
        "retrieval": {"mode": "PRESET", "phase_id": args.phase},
        "variant_id": variant["variant_id"],
        "answer_model_key": model["model_key"],
        "client_request_id": client_request_id,
    }

    response = http.post(
        f"{base}/api/projects/{args.project_id}/chat/sessions/{session_id}/messages:stream",
        json=payload,
        stream=True,
        timeout=(45, 180),
    )
    response.raise_for_status()
    header_request_id = response.headers.get("X-Request-ID")
    if header_request_id != client_request_id:
        raise RuntimeError("The server did not preserve client_request_id")
    events: list[dict[str, Any]] = []
    for line in response.iter_lines(decode_unicode=True):
        if line and line.startswith("data:"):
            events.append(json.loads(line[5:].strip()))

    errors = [event for event in events if event.get("type") == "run.error"]
    if errors:
        raise RuntimeError(f"Chat stream failed: {errors}")
    answer = "".join(
        event.get("payload", {}).get("text", "")
        for event in events
        if event.get("type") == "response.delta"
    )
    citation_events = [
        event["payload"] for event in events if event.get("type") == "citation.added"
    ]
    trace_events = [
        event["payload"] for event in events if event.get("type") == "trace.available"
    ]
    completed = [event for event in events if event.get("type") == "run.completed"]
    if not answer or len(trace_events) != 1 or len(completed) != 1:
        raise RuntimeError("Answer, Trace, or completion event is missing")
    trace_id = trace_events[0].get("trace_id")
    if not isinstance(trace_id, str) or not TRACE_ID.fullmatch(trace_id):
        raise RuntimeError("The streamed MLflow Trace ID is invalid")
    referenced = list(dict.fromkeys(CITATION_ID.findall(answer)))
    emitted = [item["citation_id"] for item in citation_events]
    if not referenced or referenced != emitted:
        raise RuntimeError(
            f"Answer citations and emitted citations differ: {referenced} != {emitted}"
        )

    history = request_json(
        http,
        "GET",
        f"{base}/api/projects/{args.project_id}/chat/sessions/{session_id}",
    )
    assistant_messages = [
        item for item in history.get("messages", []) if item.get("role") == "assistant"
    ]
    saved = assistant_messages[-1] if assistant_messages else None
    if not saved or saved.get("trace_id") != trace_id:
        raise RuntimeError("The assistant Trace ID was not saved in chat history")
    saved_citations = [item["citation_id"] for item in saved.get("citations", [])]
    if saved_citations != referenced:
        raise RuntimeError("Saved citations do not match the final answer")
    if not saved.get("trace_href"):
        raise RuntimeError("The deployed App did not return an MLflow Trace link")

    document_id = saved["citations"][0]["document_id"]
    pdf = http.get(
        f"{base}/api/projects/{args.project_id}/documents/{document_id}/content",
        timeout=45,
    )
    pdf.raise_for_status()
    if pdf.headers.get("content-type") != "application/pdf" or not pdf.content.startswith(b"%PDF"):
        raise RuntimeError("The cited document endpoint did not return a PDF")

    expected_answer_match, outcome_status, exit_code = expected_answer_outcome(
        answer,
        args.expected_answer_substring,
    )
    print(json.dumps({
        "status": outcome_status,
        "session_id": session_id,
        "request_id": header_request_id,
        "phase": args.phase,
        "variant_id": variant["variant_id"],
        "model_key": model["model_key"],
        "expected_answer_match": expected_answer_match,
        "citation_count": len(saved_citations),
        "trace_id": trace_id,
        "trace_link": True,
        "pdf_content_type": pdf.headers.get("content-type"),
    }, ensure_ascii=False, indent=2))
    if exit_code:
        print("FAILED: The answer did not contain --expected-answer-substring", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
