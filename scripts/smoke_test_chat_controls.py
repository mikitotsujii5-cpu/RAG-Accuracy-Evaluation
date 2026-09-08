#!/usr/bin/env python3
"""Verify deployed chat idempotency and durable cancellation.

The Workspace token stays inside requests headers and is never printed.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

import requests
from databricks.sdk import WorkspaceClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--app-url", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--variant-id", required=True)
    parser.add_argument("--phase", default="phase_05")
    parser.add_argument("--question", required=True)
    parser.add_argument("--replay-session-id")
    parser.add_argument("--replay-request-id")
    return parser.parse_args()


def api_json(
    http: requests.Session,
    method: str,
    url: str,
    **kwargs: Any,
) -> dict[str, Any]:
    response = http.request(method, url, timeout=45, **kwargs)
    response.raise_for_status()
    return response.json()


def read_stream(
    headers: dict[str, str],
    url: str,
    payload: dict[str, Any],
    accepted: threading.Event | None = None,
) -> tuple[str | None, list[dict[str, Any]]]:
    with requests.Session() as http:
        http.headers.update(headers)
        response = http.post(url, json=payload, stream=True, timeout=(45, 180))
        response.raise_for_status()
        if accepted:
            accepted.set()
        events: list[dict[str, Any]] = []
        for line in response.iter_lines(decode_unicode=True):
            if line and line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
        return response.headers.get("X-Request-ID"), events


def terminal_types(events: list[dict[str, Any]]) -> list[str]:
    return [
        str(event.get("type"))
        for event in events
        if event.get("type") in {"run.completed", "run.cancelled", "run.error"}
    ]


def main() -> int:
    args = parse_args()
    if bool(args.replay_session_id) != bool(args.replay_request_id):
        raise RuntimeError("Specify both replay IDs or neither")

    base = args.app_url.rstrip("/")
    headers = dict(WorkspaceClient(profile=args.profile).config.authenticate())
    http = requests.Session()
    http.headers.update(headers)
    models = api_json(http, "GET", f"{base}/api/model-options?capability=chat").get(
        "items", []
    )
    model = next(
        (item for item in models if item.get("display_name") == "GPT-5.6 Luna"),
        next((item for item in models if item.get("selectable")), None),
    )
    if not model:
        raise RuntimeError("No selectable chat model was returned")

    if args.replay_session_id:
        replay_session_id = args.replay_session_id
        replay_request_id = args.replay_request_id
    else:
        replay_session_id = api_json(
            http,
            "POST",
            f"{base}/api/projects/{args.project_id}/chat/sessions",
            json={"title": "Chat idempotency E2E"},
        )["session_id"]
        replay_request_id = str(uuid4())

    replay_payload = {
        "message": args.question,
        "rag_mode": "DETERMINISTIC",
        "retrieval": {"mode": "PRESET", "phase_id": args.phase},
        "variant_id": args.variant_id,
        "answer_model_key": model["model_key"],
        "client_request_id": replay_request_id,
    }
    replay_url = (
        f"{base}/api/projects/{args.project_id}/chat/sessions/"
        f"{replay_session_id}/messages:stream"
    )
    replay_header, replay_events = read_stream(headers, replay_url, replay_payload)
    if replay_header != replay_request_id or terminal_types(replay_events) != ["run.completed"]:
        raise RuntimeError("Idempotent replay did not return the original completed run")
    history = api_json(
        http,
        "GET",
        f"{base}/api/projects/{args.project_id}/chat/sessions/{replay_session_id}",
    )
    replay_messages = [
        item
        for item in history.get("messages", [])
        if item.get("request_id") == replay_request_id
    ]
    if [item.get("role") for item in replay_messages] != ["user", "assistant"]:
        raise RuntimeError("A replay duplicated or lost persisted chat messages")

    cancel_session_id = api_json(
        http,
        "POST",
        f"{base}/api/projects/{args.project_id}/chat/sessions",
        json={"title": "Chat cancellation E2E"},
    )["session_id"]
    cancel_request_id = str(uuid4())
    cancel_payload = {**replay_payload, "client_request_id": cancel_request_id}
    cancel_url = (
        f"{base}/api/projects/{args.project_id}/chat/sessions/"
        f"{cancel_session_id}/messages:stream"
    )
    accepted = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(read_stream, headers, cancel_url, cancel_payload, accepted)
        if not accepted.wait(60):
            raise RuntimeError("Cancellation stream was not accepted in time")
        cancel_result = api_json(
            http,
            "POST",
            f"{base}/api/projects/{args.project_id}/chat/runs/{cancel_request_id}:cancel",
            json={},
        )
        cancel_header, cancel_events = future.result(timeout=180)

    if cancel_header != cancel_request_id:
        raise RuntimeError("Cancellation stream changed client_request_id")
    if cancel_result.get("status") not in {"CANCEL_REQUESTED", "CANCELED"}:
        raise RuntimeError(f"Unexpected cancel status: {cancel_result}")
    if terminal_types(cancel_events) != ["run.cancelled"]:
        raise RuntimeError(f"Cancellation did not end with run.cancelled: {cancel_events}")

    cancel_generation_status = None
    for _ in range(30):
        history = api_json(
            http,
            "GET",
            f"{base}/api/projects/{args.project_id}/chat/sessions/{cancel_session_id}",
        )
        assistant = next(
            (
                item
                for item in history.get("messages", [])
                if item.get("role") == "assistant"
                and item.get("request_id") == cancel_request_id
            ),
            None,
        )
        cancel_generation_status = assistant.get("generation_status") if assistant else None
        if cancel_generation_status == "CANCELLED":
            break
        time.sleep(1)
    if cancel_generation_status != "CANCELLED":
        raise RuntimeError("Canceled assistant message was not persisted as CANCELLED")

    print(json.dumps({
        "status": "SUCCEEDED",
        "idempotent_replay": True,
        "replay_session_id": replay_session_id,
        "replay_request_id": replay_request_id,
        "replay_message_count": len(replay_messages),
        "cancel_session_id": cancel_session_id,
        "cancel_request_id": cancel_request_id,
        "cancel_api_status": cancel_result.get("status"),
        "cancel_terminal_event": "run.cancelled",
        "cancel_generation_status": cancel_generation_status,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
