#!/usr/bin/env python3
"""Run a credential-safe E2E smoke test for Data Preparation Build / Sync.

The short-lived Workspace credential is kept only in a ``requests.Session``
and is never printed.  This script is intentionally non-destructive: it does
not cancel runs, delete variants, or modify source documents.

Run it with the repository environment::

    app/.venv/bin/python scripts/smoke_test_preparation.py ...
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import requests
from databricks.sdk import WorkspaceClient


TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELED"})
POLL_INTERVAL_SECONDS = 10.0
HEARTBEAT_INTERVAL_SECONDS = 30.0
MAX_RETRY_SECONDS = 15.0


class ApiStatusError(RuntimeError):
    """An HTTP error that deliberately excludes response bodies and headers."""

    def __init__(self, method: str, url: str, status_code: int) -> None:
        path = urlparse(url).path
        super().__init__(f"{method} {path} returned HTTP {status_code}")
        self.status_code = status_code


class RefreshingWorkspaceSession(requests.Session):
    """Refresh a short-lived Workspace bearer token once on auth rejection."""

    def __init__(self, authenticate: Callable[[], dict[str, str]]) -> None:
        super().__init__()
        self._authenticate = authenticate
        self.refresh_authentication()

    def refresh_authentication(self) -> None:
        headers = self._authenticate()
        if not isinstance(headers, dict) or not headers.get("Authorization"):
            raise RuntimeError("The selected profile did not provide short-lived authentication")
        self.headers.update(headers)
        headers.clear()

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        response = super().request(method, url, **kwargs)
        if response.status_code not in {401, 403}:
            return response
        response.close()
        self.refresh_authentication()
        return super().request(method, url, **kwargs)


def request_json(
    session: requests.Session,
    method: str,
    url: str,
    *,
    deadline: float,
    **kwargs: Any,
) -> dict[str, Any]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("The preparation smoke-test deadline was reached")
    response = session.request(
        method,
        url,
        timeout=(10, max(1.0, min(20.0, remaining))),
        **kwargs,
    )
    if not 200 <= response.status_code < 300:
        raise ApiStatusError(method, url, response.status_code)
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"{method} {urlparse(url).path} did not return JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{method} {urlparse(url).path} returned a non-object JSON value")
    return payload


def active_item(payload: dict[str, Any]) -> dict[str, Any] | None:
    item = payload.get("item")
    if item is None:
        return None
    if not isinstance(item, dict):
        raise RuntimeError("The active preparation endpoint returned an invalid item")
    return item


def required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"Preparation response is missing {key}")
    return value


def required_run_id(payload: dict[str, Any]) -> int:
    value = payload.get("job_run_id")
    if isinstance(value, bool):
        raise RuntimeError("Preparation response contains an invalid job_run_id")
    try:
        run_id = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Preparation response is missing job_run_id") from exc
    if run_id <= 0:
        raise RuntimeError("Preparation response contains an invalid job_run_id")
    return run_id


def validate_job_url(value: Any, workspace_host: str, job_run_id: int) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError("Preparation response is missing job_run_url")
    if any(character in value for character in ("\r", "\n", "\\")):
        raise RuntimeError("job_run_url contains invalid characters")
    target = urlparse(value)
    workspace = urlparse(workspace_host)
    try:
        target_port = target.port
    except ValueError as exc:
        raise RuntimeError("job_run_url contains an invalid port") from exc
    if (
        target.scheme != "https"
        or not target.hostname
        or target.hostname.lower() != (workspace.hostname or "").lower()
        or target.username is not None
        or target.password is not None
        or target_port not in {None, 443}
    ):
        raise RuntimeError("job_run_url is not an HTTPS URL on the selected Workspace host")
    job_run_route = re.compile(
        rf"(?:^|/)(?:job|jobs)/[0-9]+/(?:run|runs)/{job_run_id}(?:$|[/?#])",
        flags=re.IGNORECASE,
    )
    if not any(job_run_route.search(route) for route in (target.path, target.fragment)):
        raise RuntimeError("job_run_url is not a Databricks Job run route for this run")
    return value


def is_transient(error: BaseException) -> bool:
    if isinstance(error, (requests.Timeout, requests.ConnectionError)):
        return True
    return isinstance(error, ApiStatusError) and error.status_code in {
        408,
        425,
        429,
        500,
        502,
        503,
        504,
    }


def sleep_with_deadline(seconds: float, deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("The preparation smoke-test deadline was reached")
    time.sleep(min(seconds, remaining))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify deployed Build / Sync deduplication and monitoring without "
            "deleting data."
        ),
        epilog=(
            "Required interpreter: app/.venv/bin/python "
            "scripts/smoke_test_preparation.py"
        ),
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument("--app-url", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument(
        "--chunk-method",
        default="SEMANTIC",
        type=str.upper,
        choices=("STANDARD", "SEMANTIC", "PARENT_CHILD"),
    )
    parser.add_argument("--chunk-size", type=int, default=256, choices=(256, 512, 1024))
    parser.add_argument("--timeout-seconds", type=int, default=2400)
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be greater than zero")
    return args


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    base = args.app_url.rstrip("/")
    app_target = urlparse(base)
    if app_target.scheme != "https" or not app_target.hostname:
        raise RuntimeError("--app-url must be an HTTPS URL")

    workspace = WorkspaceClient(profile=args.profile)
    workspace_host = str(workspace.config.host or "").rstrip("/")
    workspace_target = urlparse(workspace_host)
    if workspace_target.scheme != "https" or not workspace_target.hostname:
        raise RuntimeError("The selected profile does not contain a valid HTTPS Workspace host")

    started = time.monotonic()
    deadline = started + args.timeout_seconds
    print("Build / Sync remote E2Eを開始します。認証情報は表示しません。", flush=True)

    with RefreshingWorkspaceSession(workspace.config.authenticate) as session:

        health = request_json(session, "GET", f"{base}/api/health", deadline=deadline)
        if health.get("status") != "ok" or not health.get("databricks_ready"):
            raise RuntimeError("The deployed App health endpoint is not ready")
        print("App healthを確認しました。", flush=True)

        documents_payload = request_json(
            session,
            "GET",
            f"{base}/api/projects/{args.project_id}/documents",
            deadline=deadline,
        )
        documents = documents_payload.get("items")
        if not isinstance(documents, list):
            raise RuntimeError("The documents endpoint returned an invalid items value")
        eligible_documents = [
            item
            for item in documents
            if isinstance(item, dict)
            and str(item.get("processing_status", "")).upper() in {"PARSED", "READY"}
            and isinstance(item.get("document_id"), str)
        ][:2]
        if not eligible_documents:
            raise RuntimeError("No PARSED or READY documents are available in this Project")

        model_payload = request_json(
            session,
            "GET",
            f"{base}/api/model-options?capability=embedding",
            deadline=deadline,
        )
        models = model_payload.get("items")
        if not isinstance(models, list):
            raise RuntimeError("The model-options endpoint returned an invalid items value")
        embedding = next(
            (
                item
                for item in models
                if isinstance(item, dict)
                and item.get("selectable") is True
                and isinstance(item.get("model_key"), str)
                and item.get("model_key")
            ),
            None,
        )
        if embedding is None:
            raise RuntimeError("No selectable embedding model is available")
        print(
            f"入力候補を確認しました: documents={len(eligible_documents)} embedding=selectable",
            flush=True,
        )

        active_url = f"{base}/api/projects/{args.project_id}/preparation-runs/active"
        before_active = active_item(
            request_json(session, "GET", active_url, deadline=deadline)
        )
        payload = {
            "run_type": "BUILD_VARIANT",
            "document_ids": [item["document_id"] for item in eligible_documents],
            "configuration": {
                "chunk_method": args.chunk_method,
                "chunk_size_tokens": args.chunk_size,
                "parent_chunk_size_tokens": (
                    min(args.chunk_size * 4, 4096)
                    if args.chunk_method == "PARENT_CHILD"
                    else None
                ),
                "content_profile": "LAYOUT_PRESERVING",
                "cleaning_enabled": True,
                "semantic_metadata_enabled": True,
                "embedding_model_key": embedding["model_key"],
            },
        }
        create_url = f"{base}/api/projects/{args.project_id}/preparation-runs"

        first = request_json(
            session, "POST", create_url, deadline=deadline, json=payload
        )
        print(
            f"1回目のPOSTを受け付けました: status={first.get('status', 'UNKNOWN')}",
            flush=True,
        )
        second = request_json(
            session, "POST", create_url, deadline=deadline, json=payload
        )
        print(
            f"2回目のPOSTを受け付けました: reused={second.get('reused') is True}",
            flush=True,
        )

        prep_run_id = required_string(first, "prep_run_id")
        job_run_id = required_run_id(first)
        target_variant_id = required_string(first, "target_variant_id")
        if required_string(second, "prep_run_id") != prep_run_id:
            raise RuntimeError("The repeated POST created another prep_run_id")
        if required_run_id(second) != job_run_id:
            raise RuntimeError("The repeated POST created another job_run_id")
        if required_string(second, "target_variant_id") != target_variant_id:
            raise RuntimeError("The repeated POST returned another target_variant_id")
        if second.get("reused") is not True:
            raise RuntimeError("The repeated POST was not marked as reused")
        if before_active is not None:
            before_run_id = required_string(before_active, "prep_run_id")
            if before_run_id == prep_run_id and first.get("reused") is not True:
                raise RuntimeError(
                    "The first POST did not report reuse of the existing active run"
                )

        after_active = active_item(
            request_json(session, "GET", active_url, deadline=deadline)
        )
        if after_active is None or required_string(after_active, "prep_run_id") != prep_run_id:
            raise RuntimeError("The active preparation endpoint did not return the reused run")
        if required_run_id(after_active) != job_run_id:
            raise RuntimeError("The active preparation endpoint returned another job_run_id")

        observations: list[dict[str, Any]] = []
        last_observation: tuple[str, str, str, str] | None = None
        last_output_at = 0.0
        verified_job_url: str | None = None

        def observe(run: dict[str, Any], source: str, *, force: bool = False) -> None:
            nonlocal last_observation, last_output_at, verified_job_url
            if required_string(run, "prep_run_id") != prep_run_id:
                raise RuntimeError("The status endpoint switched to another prep_run_id")
            run_job_id = required_run_id(run)
            if run_job_id != job_run_id:
                raise RuntimeError("The status endpoint switched to another job_run_id")
            run_variant_id = required_string(run, "target_variant_id")
            if run_variant_id != target_variant_id:
                raise RuntimeError("The status endpoint switched to another target_variant_id")
            job_url = run.get("job_run_url")
            if job_url:
                verified_job_url = validate_job_url(job_url, workspace_host, job_run_id)

            status = str(run.get("status") or "UNKNOWN").upper()
            current_step = str(run.get("current_step") or "")
            job_state = str(run.get("job_state") or "")
            queue_reason = str(run.get("queue_reason") or "")
            key = (status, current_step, job_state, queue_reason)
            if key != last_observation:
                observations.append(
                    {
                        "status": status,
                        "current_step": current_step or None,
                        "job_state": job_state or None,
                        "queue_reason": queue_reason or None,
                    }
                )
                last_observation = key
                force = True
            now = time.monotonic()
            if force or now - last_output_at >= HEARTBEAT_INTERVAL_SECONDS:
                elapsed = int(now - started)
                progress = f"{run.get('completed_steps', '?')}/{run.get('total_steps', '?')}"
                print(
                    f"[{elapsed:>4}s] {source}: prep={status} job={job_state or '-'} "
                    f"step={current_step or '-'} progress={progress} "
                    f"queue={queue_reason or '-'}",
                    flush=True,
                )
                last_output_at = now

        observe(first, "POST-1")
        observe(second, "POST-2")
        observe(after_active, "active")

        run = after_active
        failures = 0
        status_url = f"{base}/api/projects/{args.project_id}/preparation-runs/{prep_run_id}"
        while str(run.get("status") or "").upper() not in TERMINAL_STATUSES:
            sleep_with_deadline(POLL_INTERVAL_SECONDS, deadline)
            try:
                run = request_json(session, "GET", status_url, deadline=deadline)
                failures = 0
                observe(run, "poll")
            except (requests.RequestException, ApiStatusError) as exc:
                if not is_transient(exc):
                    raise
                failures += 1
                delay = min(MAX_RETRY_SECONDS, 2 ** min(failures, 5))
                print(
                    f"[{int(time.monotonic() - started):>4}s] 状態取得に一時失敗。"
                    f"{int(delay)}秒後に再試行します。",
                    flush=True,
                )
                sleep_with_deadline(delay, deadline)

        final_status = str(run.get("status") or "").upper()
        if final_status != "SUCCEEDED":
            raise RuntimeError(f"Preparation finished with status {final_status}")
        if not any(item["status"] not in TERMINAL_STATUSES for item in observations[:-1]):
            raise RuntimeError("No non-terminal preparation state was observed")
        if len(observations) < 2:
            raise RuntimeError("No preparation state transition was observed")
        if verified_job_url is None:
            raise RuntimeError("No valid Workspace Job URL was observed")

        variant_ready = False
        last_variant_output = 0.0
        while not variant_ready:
            variants_payload = request_json(
                session,
                "GET",
                f"{base}/api/projects/{args.project_id}/variants",
                deadline=deadline,
            )
            variants = variants_payload.get("items")
            if not isinstance(variants, list):
                raise RuntimeError("The variants endpoint returned an invalid items value")
            match = next(
                (
                    item
                    for item in variants
                    if isinstance(item, dict)
                    and item.get("variant_id") == target_variant_id
                ),
                None,
            )
            variant_ready = bool(
                match and str(match.get("status") or "").upper() == "READY"
            )
            if variant_ready:
                break
            now = time.monotonic()
            if now - last_variant_output >= HEARTBEAT_INTERVAL_SECONDS:
                print(
                    f"[{int(now - started):>4}s] Variant一覧への反映を待っています。",
                    flush=True,
                )
                last_variant_output = now
            sleep_with_deadline(5.0, deadline)

        return {
            "status": "SUCCEEDED",
            "project_id": args.project_id,
            "prep_run_id": prep_run_id,
            "job_run_id": job_run_id,
            "target_variant_id": target_variant_id,
            "existing_active_before_post": before_active is not None,
            "first_post_reused": first.get("reused") is True,
            "second_post_reused": True,
            "document_count": len(eligible_documents),
            "chunk_method": args.chunk_method,
            "chunk_size": args.chunk_size,
            "job_url_verified": True,
            "variant_ready": True,
            "elapsed_seconds": round(time.monotonic() - started, 1),
            "observed_transitions": observations,
        }


def main() -> int:
    args = parse_args()
    try:
        result = run_smoke(args)
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
