#!/usr/bin/env python3
"""Register and evaluate one domain-neutral RAG question in all five phases."""

from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import requests
from databricks.sdk import WorkspaceClient


PHASE_IDS = [f"phase_{number:02d}" for number in range(1, 6)]
TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELED", "PARTIAL"})


class ApiStatusError(RuntimeError):
    """HTTP failure that excludes credentials and untrusted response bodies."""

    def __init__(self, method: str, url: str, status_code: int, code: str | None) -> None:
        suffix = f" ({code})" if code else ""
        super().__init__(
            f"{method} {urlparse(url).path} returned HTTP {status_code}{suffix}"
        )
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
        # Evaluation may run for tens of minutes. Refresh safe status/result
        # reads, but never replay a state-changing POST after an uncertain 401.
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


def require_uuid4(value: str, option: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{option} must be a canonical UUIDv4") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise RuntimeError(f"{option} must be a canonical UUIDv4")
    return str(parsed)


def request_json(
    session: requests.Session,
    method: str,
    url: str,
    *,
    timeout: float = 90,
    **kwargs: Any,
) -> dict[str, Any]:
    response = session.request(method, url, timeout=timeout, **kwargs)
    if not 200 <= response.status_code < 300:
        try:
            error = response.json().get("error", {})
        except (ValueError, AttributeError):
            error = {}
        code = error.get("code") if isinstance(error, dict) else None
        raise ApiStatusError(
            method,
            url,
            response.status_code,
            str(code) if code else None,
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{method} {urlparse(url).path} did not return JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"{method} {urlparse(url).path} returned a non-object JSON value"
        )
    return payload


def choose_model(items: Any, preferred_display_name: str) -> dict[str, Any]:
    if not isinstance(items, list):
        raise RuntimeError("The model endpoint returned invalid items")
    selectable = [
        item
        for item in items
        if isinstance(item, dict)
        and item.get("selectable") is True
        and isinstance(item.get("model_key"), str)
    ]
    preferred = next(
        (item for item in selectable if item.get("display_name") == preferred_display_name),
        None,
    )
    if preferred is not None:
        return preferred
    if selectable:
        return selectable[0]
    raise RuntimeError("No selectable model is available")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--app-url", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--variant-id", required=True)
    parser.add_argument("--dataset-version", default="security-policy-v1")
    parser.add_argument("--timeout-seconds", type=int, default=2400)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.timeout_seconds <= 0:
        raise RuntimeError("--timeout-seconds must be positive")
    project_id = require_uuid4(args.project_id, "--project-id")
    document_id = require_uuid4(args.document_id, "--document-id")
    variant_id = require_uuid4(args.variant_id, "--variant-id")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", args.dataset_version):
        raise RuntimeError("--dataset-version is invalid")
    workspace = WorkspaceClient(profile=args.profile)
    base = validate_app_base(args.app_url, str(workspace.config.host or ""))
    session = RefreshingWorkspaceSession(workspace.config.authenticate)

    health = request_json(session, "GET", f"{base}/api/health")
    if health.get("status") != "ok" or health.get("databricks_ready") is not True:
        raise RuntimeError("The deployed App is not ready")

    documents = request_json(
        session,
        "GET",
        f"{base}/api/projects/{project_id}/documents",
    ).get("items")
    if not isinstance(documents, list):
        raise RuntimeError("The documents endpoint returned invalid items")
    source_document = next(
        (
            item
            for item in documents
            if isinstance(item, dict) and item.get("document_id") == document_id
        ),
        None,
    )
    if not source_document or str(source_document.get("processing_status") or "").upper() not in {
        "PARSED",
        "READY",
    }:
        raise RuntimeError("The generic source document is not parsed in this Project")
    unexpected_legacy = {
        key: source_document.get(key)
        for key in ("model", "model_year", "document_type", "vehicle_category")
        if source_document.get(key) is not None
    }
    generic_values = [
        source_document.get("category"),
        source_document.get("tags"),
        source_document.get("document_date"),
        source_document.get("source"),
        source_document.get("custom_metadata"),
    ]
    if unexpected_legacy or not any(generic_values):
        raise RuntimeError("The selected document is not a verified generic-metadata document")

    variants = request_json(
        session,
        "GET",
        f"{base}/api/projects/{project_id}/variants",
    ).get("items")
    selected_variant = next(
        (
            item
            for item in variants or []
            if isinstance(item, dict)
            and item.get("variant_id") == variant_id
            and str(item.get("status") or "").upper() == "READY"
        ),
        None,
    )
    if selected_variant is None:
        raise RuntimeError("The selected generic Project Variant was not returned as READY")

    case_payload = {
        "question": "CSIRTへの一次報告期限は？",
        "expected_answer": "検知から30分以内",
        "expected_facts": ["CSIRTへの一次報告はインシデント検知から30分以内"],
        "relevant_document_id": document_id,
        "relevant_pages": [2],
        "question_type": "deadline",
        "is_answerable": True,
        "language": "ja",
        "dataset_version": args.dataset_version,
        "dataset_split": "development",
    }
    case_url = f"{base}/api/projects/{project_id}/evaluation-cases"
    existing_cases = request_json(
        session,
        "GET",
        case_url,
        params={
            "dataset_version": args.dataset_version,
            "dataset_split": "development",
        },
    ).get("items")
    if not isinstance(existing_cases, list):
        raise RuntimeError("The evaluation cases endpoint returned invalid items")
    matching_cases = [
        item
        for item in existing_cases
        if isinstance(item, dict)
        and item.get("question") == case_payload["question"]
        and item.get("document_id") == document_id
    ]
    if len(matching_cases) > 1:
        raise RuntimeError("The generic evaluation case is duplicated")
    evaluation_case = matching_cases[0] if matching_cases else None
    if evaluation_case is None:
        evaluation_case = request_json(
            session,
            "POST",
            case_url,
            json=case_payload,
        )
    expected_case_fields = {
        "question": case_payload["question"],
        "expected_answer": case_payload["expected_answer"],
        "expected_facts": case_payload["expected_facts"],
        "document_id": document_id,
        "relevant_pages": case_payload["relevant_pages"],
        "question_type": case_payload["question_type"],
        "is_answerable": case_payload["is_answerable"],
        "language": case_payload["language"],
        "dataset_version": case_payload["dataset_version"],
        "dataset_split": case_payload["dataset_split"],
    }
    mismatches = {
        key: {"expected": expected, "actual": evaluation_case.get(key)}
        for key, expected in expected_case_fields.items()
        if evaluation_case.get(key) != expected
    }
    if mismatches:
        raise RuntimeError(
            "The evaluation case does not match the smoke-test contract: "
            + json.dumps(mismatches, ensure_ascii=False)
        )
    if evaluation_case.get("document_id") != document_id:
        raise RuntimeError("The evaluation case did not retain the Project document")

    datasets = request_json(
        session,
        "GET",
        f"{base}/api/projects/{project_id}/evaluation-datasets",
    ).get("items")
    if not isinstance(datasets, list):
        raise RuntimeError("The new evaluation dataset was not returned")
    selected_dataset = next(
        (
            item
            for item in datasets
            if isinstance(item, dict)
            and item.get("dataset_version") == args.dataset_version
            and item.get("dataset_split") == "development"
        ),
        None,
    )
    try:
        dataset_case_count = int(selected_dataset.get("case_count") or 0)
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeError("The new evaluation dataset has an invalid case count") from exc
    if dataset_case_count < 1:
        raise RuntimeError("The new evaluation dataset was not returned")

    projects = request_json(session, "GET", f"{base}/api/projects").get("items")
    project = next(
        (
            item
            for item in projects or []
            if isinstance(item, dict) and item.get("project_id") == project_id
        ),
        None,
    )
    if not project or project.get("dataset_version") != args.dataset_version:
        raise RuntimeError("The Project did not switch to the new evaluation dataset")

    answer_model = choose_model(
        request_json(
            session, "GET", f"{base}/api/model-options?capability=chat"
        ).get("items"),
        "GPT-5.6 Luna",
    )
    judge_model = choose_model(
        request_json(
            session, "GET", f"{base}/api/model-options?capability=judge"
        ).get("items"),
        "GPT-5.6 Terra",
    )
    run_payload = {
        "phase_ids": PHASE_IDS,
        "trial_count": 1,
        "dataset_version": args.dataset_version,
        "dataset_split": "development",
        "variant_id": variant_id,
        "answer_model_key": answer_model["model_key"],
        "judge_model_key": judge_model["model_key"],
        "final_k": 10,
    }
    idempotency_key = "generic_eval_" + uuid.uuid4().hex
    run = request_json(
        session,
        "POST",
        f"{base}/api/projects/{project_id}/evaluation-runs",
        json=run_payload,
        headers={"Idempotency-Key": idempotency_key},
    )
    eval_run_id = run.get("eval_run_id")
    eval_run_id = require_uuid4(str(eval_run_id), "eval_run_id")

    started = time.monotonic()
    deadline = started + args.timeout_seconds
    last_observation: tuple[str, tuple[tuple[str, str, int, int], ...]] | None = None
    observations: list[dict[str, Any]] = []
    status_url = f"{base}/api/projects/{project_id}/evaluation-runs/{eval_run_id}"
    while time.monotonic() < deadline:
        run = request_json(session, "GET", status_url)
        status = str(run.get("status") or "UNKNOWN").upper()
        phases = run.get("phases")
        if not isinstance(phases, list):
            raise RuntimeError("The evaluation status returned invalid phases")
        phase_state = tuple(
            (
                str(item.get("phase_id")),
                str(item.get("status")),
                int(item.get("completed_trials") or 0),
                int(item.get("expected_trials") or 0),
            )
            for item in phases
            if isinstance(item, dict)
        )
        observation_key = (status, phase_state)
        if observation_key != last_observation:
            observation = {
                "elapsed_seconds": round(time.monotonic() - started, 1),
                "status": status,
                "phases": [
                    {
                        "phase_id": phase_id,
                        "status": phase_status,
                        "completed": completed,
                        "expected": expected,
                    }
                    for phase_id, phase_status, completed, expected in phase_state
                ],
            }
            observations.append(observation)
            print(json.dumps(observation, ensure_ascii=False), flush=True)
            last_observation = observation_key
        if status in TERMINAL_STATUSES:
            break
        time.sleep(12)
    else:
        raise TimeoutError("The evaluation smoke-test deadline was reached")

    if str(run.get("status") or "").upper() != "SUCCEEDED":
        raise RuntimeError(f"Evaluation finished with status {run.get('status')}")
    final_phase_states = {
        str(item.get("phase_id")): item
        for item in run.get("phases") or []
        if isinstance(item, dict)
    }
    if set(final_phase_states) != set(PHASE_IDS) or any(
        str(item.get("status") or "").upper() != "SUCCEEDED"
        or int(item.get("completed_trials") or 0) != dataset_case_count
        or int(item.get("expected_trials") or 0) != dataset_case_count
        for item in final_phase_states.values()
    ):
        raise RuntimeError(
            "The five evaluation phases did not complete the expected number of cases and trials"
        )
    results = request_json(
        session,
        "GET",
        f"{base}/api/projects/{project_id}/evaluation-runs/{eval_run_id}/results",
    )
    metrics = results.get("metrics")
    suggestions = results.get("suggestions")
    if not isinstance(metrics, list) or not all(isinstance(item, dict) for item in metrics):
        raise RuntimeError("The evaluation results returned invalid metrics")
    if not isinstance(suggestions, list) or not all(
        isinstance(item, dict) for item in suggestions
    ):
        raise RuntimeError("The evaluation results returned invalid suggestions")
    metric_phases = {
        item.get("phase_id")
        for item in metrics or []
        if isinstance(item, dict)
    }
    suggestion_phases = {
        item.get("phase_id")
        for item in suggestions or []
        if isinstance(item, dict)
    }
    if metric_phases != set(PHASE_IDS):
        raise RuntimeError("Metrics do not cover all five phases")
    if suggestion_phases != set(PHASE_IDS):
        raise RuntimeError("LLM suggestions do not cover all five phases")
    if any(float(item.get("error_rate") or 0) != 0 for item in metrics):
        raise RuntimeError("At least one evaluation phase contains an error")
    quality_fields = ("answer_correctness", "groundedness", "citation_correctness")
    if any(
        int(item.get("trials") or 0) != dataset_case_count
        or any(
            item.get(field) is None or not 0.0 <= float(item[field]) <= 1.0
            for field in quality_fields
        )
        for item in metrics
    ):
        raise RuntimeError("Evaluation quality metrics are missing or invalid")
    metric_by_phase = {str(item["phase_id"]): item for item in metrics}
    final_metric = metric_by_phase["phase_05"]
    retrieval_fields = ("recall_at_10", "precision_at_10", "ndcg_at_10")
    if any(
        final_metric.get(field) is None
        or not 0.0 <= float(final_metric[field]) <= 1.0
        for field in retrieval_fields
    ):
        raise RuntimeError("Phase 5 retrieval metrics are missing or invalid")
    if float(final_metric["recall_at_10"]) <= 0 or float(final_metric["ndcg_at_10"]) <= 0:
        raise RuntimeError("Phase 5 did not retrieve the registered generic PDF evidence")
    if any(float(final_metric[field]) < 1.0 for field in quality_fields):
        raise RuntimeError("Phase 5 answer, grounding, or citation quality did not pass")
    eval_case_id = evaluation_case.get("eval_case_id")
    if any(
        not isinstance(item.get("evidence_case_ids"), list)
        or not item["evidence_case_ids"]
        or item.get("generation") != "LLM"
        or not isinstance(item.get("proposed_change"), str)
        or not item["proposed_change"].strip()
        for item in suggestions
    ):
        raise RuntimeError("An LLM suggestion is missing evidence or a proposed change")

    session.close()
    print(
        json.dumps(
            {
                "status": "SUCCEEDED",
                "eval_case_id": eval_case_id,
                "eval_run_id": eval_run_id,
                "dataset_version": args.dataset_version,
                "dataset_auto_selected": True,
                "answer_model_key": answer_model["model_key"],
                "judge_model_key": judge_model["model_key"],
                "phase_metrics": len(metrics),
                "phase_suggestions": len(suggestions),
                "dataset_case_count": dataset_case_count,
                "generic_document_verified": True,
                "variant_ready_verified": True,
                "phase_trial_contract_verified": True,
                "quality_metrics_verified": True,
                "suggestion_evidence_verified": True,
                "phase_05_retrieval_verified": True,
                "observations": observations,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
