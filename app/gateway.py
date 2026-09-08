"""Small, mockable boundary around Databricks APIs.

The rest of the app never receives a WorkspaceClient and never accepts a
physical table, index, Volume, or endpoint name from a browser request.
"""

from __future__ import annotations

import io
import json
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlparse

from errors import ResourceNotReadyError
from settings import Settings, SettingsError


class DatabricksCallError(ResourceNotReadyError):
    """Sanitized Databricks failure; raw details stay in server logs only."""


def as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "as_dict"):
        result = value.as_dict()
        if isinstance(result, dict):
            return result
    if hasattr(value, "__dict__"):
        return {key: item for key, item in vars(value).items() if not key.startswith("_")}
    raise TypeError("Databricks API response is not mapping-like")


class DatabricksGateway:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._workspace: Any | None = None

    @property
    def workspace(self) -> Any:
        if self._workspace is None:
            try:
                from databricks.sdk import WorkspaceClient
            except ImportError as exc:
                raise ResourceNotReadyError(
                    "Databricks SDKがインストールされていません。requirements.txtを反映してください。"
                ) from exc
            try:
                self._workspace = WorkspaceClient()
            except Exception as exc:  # pragma: no cover - credential implementation dependent
                raise DatabricksCallError(
                    "Databricksへの接続を初期化できません。Appの認証設定を確認してください。"
                ) from exc
        return self._workspace

    def execute_sql(self, statement: str, *, timeout_seconds: int = 50) -> dict[str, Any]:
        if not self.settings.warehouse_id:
            raise ResourceNotReadyError(
                "SQL Warehouseが未設定です。",
                missing=["DATABRICKS_WAREHOUSE_ID"],
            )
        try:
            from databricks.sdk.service.sql import (
                Disposition,
                ExecuteStatementRequestOnWaitTimeout,
                Format,
            )

            response_obj = self.workspace.statement_execution.execute_statement(
                warehouse_id=self.settings.warehouse_id,
                statement=statement,
                catalog=self.settings.uc_catalog,
                schema=self.settings.uc_schema,
                wait_timeout="20s",
                on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CONTINUE,
                disposition=Disposition.INLINE,
                format=Format.JSON_ARRAY,
            )
            response = as_dict(response_obj)
            statement_id = response.get("statement_id")
            deadline = time.monotonic() + timeout_seconds

            while _statement_state(response) in {"PENDING", "RUNNING"}:
                if not statement_id or time.monotonic() >= deadline:
                    if statement_id:
                        self._cancel_statement(statement_id)
                    raise DatabricksCallError(
                        "SQL処理が制限時間内に完了しなかったため取り消しました。Warehouseの状態を確認してください。"
                    )
                time.sleep(0.4)
                response = as_dict(
                    self.workspace.statement_execution.get_statement(statement_id)
                )

            state = _statement_state(response)
            if state != "SUCCEEDED":
                raise DatabricksCallError(
                    "SQL処理に失敗しました。Table権限とWarehouseの実行ログを確認してください。"
                )
            return self._collect_statement_chunks(response)
        except ResourceNotReadyError:
            raise
        except ImportError as exc:
            raise ResourceNotReadyError(
                "Databricks SDKがインストールされていません。requirementsを反映してください。"
            ) from exc
        except Exception as exc:
            if isinstance(exc, DatabricksCallError):
                raise
            raise DatabricksCallError(
                "SQL処理を実行できませんでした。WarehouseとApp権限を確認してください。"
            ) from exc

    def _cancel_statement(self, statement_id: str) -> None:
        try:
            self.workspace.statement_execution.cancel_execution(statement_id)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                state = _statement_state(
                    as_dict(self.workspace.statement_execution.get_statement(statement_id))
                )
                if state not in {"PENDING", "RUNNING"}:
                    return
                time.sleep(0.2)
        except Exception:
            # The user receives the timeout. Cancellation failure belongs in the
            # Databricks audit/job logs and must not expose statement details.
            return

    def _collect_statement_chunks(self, response: dict[str, Any]) -> dict[str, Any]:
        statement_id = response.get("statement_id")
        result = response.setdefault("result", {})
        rows = list(result.get("data_array") or [])
        next_index = result.get("next_chunk_index")
        while next_index is not None:
            if not statement_id:
                raise DatabricksCallError("SQL応答のstatement IDを確認できませんでした。")
            chunk = as_dict(
                self.workspace.statement_execution.get_statement_result_chunk_n(
                    statement_id, int(next_index)
                )
            )
            rows.extend(chunk.get("data_array") or [])
            next_index = chunk.get("next_chunk_index")
        result["data_array"] = rows
        result["next_chunk_index"] = None
        return response

    def query(self, statement: str) -> list[dict[str, Any]]:
        response = self.execute_sql(statement)
        manifest = response.get("manifest") or {}
        schema = manifest.get("schema") or {}
        columns = schema.get("columns") or manifest.get("columns") or []
        names = [column.get("name") for column in columns if isinstance(column, dict)]
        arrays = (response.get("result") or {}).get("data_array") or []
        if not names and arrays:
            raise DatabricksCallError("SQL応答の列情報を確認できませんでした。")
        rows: list[dict[str, Any]] = []
        for values in arrays:
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
                raise DatabricksCallError("SQL応答の行形式が不正です。")
            if len(values) != len(names):
                raise DatabricksCallError("SQL応答の列数が一致しません。")
            rows.append(dict(zip(names, values, strict=True)))
        return rows

    def upload_volume_file(self, path: str, data: bytes) -> None:
        if not path.startswith(self.settings.volume_path + "/projects/"):
            raise ValueError("Upload path escaped the configured Volume")
        try:
            self.workspace.files.upload(path, io.BytesIO(data), overwrite=False)
        except Exception as exc:
            raise DatabricksCallError(
                "PDFをUnity Catalog Volumeへ保存できませんでした。Volume権限を確認してください。"
            ) from exc

    def download_volume_file(self, path: str) -> bytes:
        if not path.startswith(self.settings.volume_path + "/projects/"):
            raise ValueError("Download path escaped the configured Volume")
        try:
            response = self.workspace.files.download(path)
            contents = getattr(response, "contents", None)
            if contents is None and isinstance(response, dict):
                contents = response.get("contents")
            if hasattr(contents, "read"):
                return contents.read()
            if isinstance(contents, bytes):
                return contents
            raise RuntimeError("file content missing")
        except Exception as exc:
            raise DatabricksCallError(
                "PDFを読み込めませんでした。Volume権限とファイルの存在を確認してください。"
            ) from exc

    def run_job(self, job_id: str | None, parameter_name: str, parameter_value: str) -> int:
        if not job_id:
            missing = "PREP_JOB_ID" if parameter_name == "prep_run_id" else "EVAL_JOB_ID"
            raise ResourceNotReadyError(
                f"{missing} が未設定のため、非同期処理を開始できません。",
                missing=[missing],
            )
        try:
            result = self.workspace.jobs.run_now(
                job_id=int(job_id),
                job_parameters={parameter_name: parameter_value},
                # The persisted run UUID is stable across browser retries. If
                # two App requests race, Databricks returns the same Job run
                # instead of launching duplicate compute.
                idempotency_token=f"{parameter_name}:{parameter_value}",
            )
            run_id = getattr(result, "run_id", None)
            if run_id is None and isinstance(result, dict):
                run_id = result.get("run_id")
            if run_id is None:
                raise RuntimeError("run_id missing")
            return int(run_id)
        except ResourceNotReadyError:
            raise
        except Exception as exc:
            raise DatabricksCallError(
                "Databricks Jobを開始できませんでした。Job権限と設定を確認してください。"
            ) from exc

    def get_job_run(self, run_id: int) -> dict[str, Any]:
        """Return a small, sanitized view of one Lakeflow Job run.

        Databricks ``state_message`` values can contain infrastructure details,
        so callers receive only controlled messages and reason codes.  The UI
        link is returned only when it points back to the configured Workspace.
        """

        if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
            raise ValueError("run_id must be a positive integer")
        try:
            workspace = self.workspace
            payload = as_dict(workspace.jobs.get_run(run_id=run_id))
            # Databricks Apps can use an internal API host that differs from
            # the browser-facing Workspace URL. Prefer the explicitly bound
            # UI host so the Job link remains same-Workspace and clickable.
            configured_host = self.settings.workspace_ui_host or self.settings.databricks_host
            if not configured_host:
                configured_host = getattr(getattr(workspace, "config", None), "host", None)
            return _normalize_job_run(
                payload,
                expected_run_id=run_id,
                expected_host=configured_host,
            )
        except ResourceNotReadyError:
            raise
        except Exception as exc:
            if isinstance(exc, DatabricksCallError):
                raise
            raise DatabricksCallError(
                "Lakeflow Jobの状態を取得できませんでした。Job権限を確認してください。"
            ) from exc

    def similarity_search(
        self,
        *,
        index_name: str,
        query_text: str,
        query_type: str,
        columns: list[str],
        num_results: int,
        filters: dict[str, Any] | None,
        reranking: bool,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "query_text": query_text,
            "query_type": query_type,
            "columns": columns,
            "num_results": num_results,
            "debug_level": 1,
        }
        if filters:
            kwargs["filters"] = filters

        try:
            if reranking:
                from databricks.ai_search.reranker import DatabricksReranker

                kwargs["reranker"] = DatabricksReranker(
                    columns_to_rerank=["title", "chunk_to_retrieve"]
                )
            client = self._ai_search_client()
            index = client.get_index(index_name=index_name)
            raw = index.similarity_search(**kwargs)
            return as_dict(raw) if not isinstance(raw, dict) else raw
        except ImportError:
            if reranking:
                raise ResourceNotReadyError(
                    "Rerankingにはdatabricks-ai-searchパッケージが必要です。"
                )
            return self._sdk_vector_search(
                index_name=index_name,
                query_text=query_text,
                query_type=query_type,
                columns=columns,
                num_results=num_results,
                filters=filters,
            )
        except ResourceNotReadyError:
            raise
        except Exception as exc:
            raise DatabricksCallError(
                "AI Searchを実行できませんでした。Indexの同期状態とSELECT権限を確認してください。"
            ) from exc

    def _ai_search_client(self) -> Any:
        """Create an AI Search client from the app's current OAuth bearer token.

        ``AISearchClient()`` without arguments only auto-detects notebook-style
        credentials. Databricks Apps authenticates through ``WorkspaceClient``;
        reuse that client's short-lived Authorization header in memory without
        storing it on this long-lived gateway or emitting it to logs.
        """
        from databricks.ai_search.client import AISearchClient

        config = self.workspace.config
        headers = config.authenticate()
        authorization = headers.get("Authorization", "")
        prefix = "Bearer "
        if not authorization.startswith(prefix) or len(authorization) <= len(prefix):
            raise DatabricksCallError(
                "AI Search用のApp認証トークンを取得できませんでした。"
            )
        return AISearchClient(
            workspace_url=config.host,
            personal_access_token=authorization[len(prefix):],
            disable_notice=True,
        )

    def _sdk_vector_search(
        self,
        *,
        index_name: str,
        query_text: str,
        query_type: str,
        columns: list[str],
        num_results: int,
        filters: dict[str, Any] | None,
    ) -> dict[str, Any]:
        try:
            kwargs: dict[str, Any] = {
                "index_name": index_name,
                "columns": columns,
                "query_text": query_text,
                "query_type": query_type,
                "num_results": num_results,
            }
            if filters:
                kwargs["filters_json"] = json.dumps(filters, ensure_ascii=False)
            result = self.workspace.vector_search_indexes.query_index(**kwargs)
            return as_dict(result)
        except Exception as exc:
            raise DatabricksCallError(
                "AI Searchを実行できませんでした。Indexの同期状態とSELECT権限を確認してください。"
            ) from exc

    def invoke_model(
        self,
        endpoint_name: str,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 900,
        user_access_token: str | None = None,
    ) -> dict[str, Any]:
        if not endpoint_name:
            raise ResourceNotReadyError("利用できるLLM endpointがありません。")
        try:
            from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

            roles = {
                "system": ChatMessageRole.SYSTEM,
                "user": ChatMessageRole.USER,
                "assistant": ChatMessageRole.ASSISTANT,
            }
            typed_messages = [
                ChatMessage(role=roles[item["role"]], content=item["content"])
                for item in messages
            ]
            serving_client = self.workspace
            if user_access_token:
                # Keep the forwarded credential request-scoped.  It is never
                # stored on the long-lived Gateway or written to logs/Trace.
                from databricks.sdk import WorkspaceClient

                client_options: dict[str, str] = {
                    "token": user_access_token,
                    # App OAuth client credentials are also present in the
                    # runtime environment. Explicit PAT-style bearer auth keeps
                    # this request-scoped OBO token from conflicting with them.
                    "auth_type": "pat",
                }
                if self.settings.databricks_host:
                    client_options["host"] = self.settings.databricks_host
                serving_client = WorkspaceClient(**client_options)
            response = serving_client.serving_endpoints.query(
                name=endpoint_name,
                messages=typed_messages,
                max_tokens=max_tokens,
            )
            return as_dict(response)
        except ResourceNotReadyError:
            raise
        except Exception as exc:
            raise DatabricksCallError(
                "回答LLMを呼び出せませんでした。Endpointの状態とCAN QUERY権限を確認してください。"
            ) from exc


_JOB_LIFECYCLE_STATES = frozenset(
    {
        "BLOCKED",
        "INTERNAL_ERROR",
        "PENDING",
        "QUEUED",
        "RUNNING",
        "SKIPPED",
        "TERMINATED",
        "TERMINATING",
        "WAITING_FOR_RETRY",
    }
)
_JOB_RESULT_STATES = frozenset(
    {
        "CANCELED",
        "DISABLED",
        "EXCLUDED",
        "FAILED",
        "MAXIMUM_CONCURRENT_RUNS_REACHED",
        "SUCCESS",
        "SUCCESS_WITH_FAILURES",
        "TIMEDOUT",
        "UPSTREAM_CANCELED",
        "UPSTREAM_FAILED",
    }
)


def _normalize_job_run(
    payload: Mapping[str, Any],
    *,
    expected_run_id: int,
    expected_host: str | None,
) -> dict[str, Any]:
    """Normalize a Jobs API response without forwarding provider messages."""

    try:
        actual_run_id = int(payload.get("run_id") or payload.get("job_run_id"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Jobs API response did not contain a run ID") from exc
    if actual_run_id != expected_run_id:
        raise ValueError("Jobs API response returned another run ID")

    legacy_state = _mapping(payload.get("state"))
    current_status = _mapping(payload.get("status"))
    lifecycle = _enum_name(legacy_state.get("life_cycle_state"))
    if lifecycle not in _JOB_LIFECYCLE_STATES:
        lifecycle = _enum_name(current_status.get("state"))
    if lifecycle not in _JOB_LIFECYCLE_STATES:
        lifecycle = "UNKNOWN"

    result_state = _enum_name(legacy_state.get("result_state"))
    if result_state == "CANCELLED":
        result_state = "CANCELED"
    termination = _mapping(current_status.get("termination_details"))
    if result_state not in _JOB_RESULT_STATES:
        result_state = _enum_name(termination.get("code"))
    if result_state == "CANCELLED":
        result_state = "CANCELED"
    if result_state not in _JOB_RESULT_STATES:
        result_state = None

    task_states: list[tuple[str, str]] = []
    tasks = payload.get("tasks")
    if isinstance(tasks, Sequence) and not isinstance(tasks, (str, bytes, bytearray)):
        for raw_task in tasks:
            task = _mapping(raw_task)
            task_legacy = _mapping(task.get("state"))
            task_status = _mapping(task.get("status"))
            task_state = _enum_name(task_legacy.get("life_cycle_state"))
            if task_state == "UNKNOWN":
                task_state = _enum_name(task_status.get("state"))
            # This value is used for classification only and is never returned.
            provider_message = str(task_legacy.get("state_message") or "")
            task_states.append((task_state, provider_message.casefold()))

    queue_reason: str | None = None
    if lifecycle == "QUEUED":
        queue_reason = "WAITING_FOR_JOB_CAPACITY"
    elif lifecycle == "WAITING_FOR_RETRY":
        queue_reason = "RETRY_WAIT"
    elif lifecycle == "BLOCKED":
        queue_reason = "BLOCKED"
    elif lifecycle == "PENDING":
        queue_reason = "COMPUTE_STARTING"
    else:
        pending_messages = [message for state, message in task_states if state == "PENDING"]
        if pending_messages:
            if any("librar" in message or "environment" in message for message in pending_messages):
                queue_reason = "ENVIRONMENT_STARTING"
            elif any("cluster" in message or "compute" in message for message in pending_messages):
                queue_reason = "COMPUTE_STARTING"
            else:
                queue_reason = "TASK_STARTING"

    if queue_reason == "WAITING_FOR_JOB_CAPACITY":
        message = "先行するデータ準備Jobの完了を待っています。"
    elif queue_reason == "COMPUTE_STARTING":
        message = "Databricksコンピュートを起動しています。数分かかることがあります。"
    elif queue_reason == "ENVIRONMENT_STARTING":
        message = "Jobの実行環境とライブラリを準備しています。"
    elif queue_reason == "TASK_STARTING":
        message = "データ準備Taskの開始を待っています。"
    elif queue_reason == "RETRY_WAIT":
        message = "一時的な問題の後、Jobの再試行を待っています。"
    elif queue_reason == "BLOCKED":
        message = "Jobは前提条件の完了を待っています。"
    elif lifecycle == "RUNNING":
        message = "データ準備Jobを実行しています。"
    elif lifecycle == "TERMINATING":
        message = "Jobを終了しています。"
    elif lifecycle == "TERMINATED" and result_state == "SUCCESS":
        message = "データ準備Jobが完了しました。"
    elif lifecycle == "TERMINATED" and result_state == "CANCELED":
        message = "データ準備Jobはキャンセルされました。"
    elif lifecycle in {"TERMINATED", "INTERNAL_ERROR", "SKIPPED"}:
        message = "データ準備Jobは完了できませんでした。"
    else:
        message = "Lakeflow Jobの状態を確認中です。"

    return {
        "job_run_id": actual_run_id,
        "job_run_url": _safe_job_run_url(
            payload.get("run_page_url"),
            expected_host=expected_host,
            expected_run_id=expected_run_id,
        ),
        "job_state": lifecycle,
        "job_state_message": message,
        "queue_reason": queue_reason,
        # Repository code uses this sanitized enum to reconcile Jobs that fail
        # before the notebook can update the Delta control row.
        "result_state": result_state,
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _enum_name(value: Any) -> str:
    if value is None:
        return "UNKNOWN"
    raw = getattr(value, "value", value)
    return str(raw).rsplit(".", 1)[-1].strip().upper() or "UNKNOWN"


def _safe_job_run_url(
    value: Any,
    *,
    expected_host: str | None,
    expected_run_id: int,
) -> str | None:
    if not isinstance(value, str) or not isinstance(expected_host, str):
        return None
    if any(character in value for character in ("\r", "\n", "\\")):
        return None
    try:
        target = urlparse(value)
        workspace = urlparse(expected_host)
        if (
            target.scheme != "https"
            or workspace.scheme != "https"
            or not target.hostname
            or not workspace.hostname
            or target.hostname.casefold() != workspace.hostname.casefold()
            or target.username is not None
            or target.password is not None
            or target.port not in {None, 443}
        ):
            return None
    except ValueError:
        return None
    location = "?".join((target.path, target.query)) + "#" + target.fragment
    run_pattern = rf"(?:run|runs)(?:/|=){expected_run_id}(?:$|[^0-9])"
    return value if re.search(run_pattern, location, flags=re.IGNORECASE) else None


def _statement_state(response: Mapping[str, Any]) -> str:
    status = response.get("status") or {}
    state = status.get("state") if isinstance(status, Mapping) else None
    return str(state or "UNKNOWN").upper()


def sql_string(value: str | None) -> str:
    if value is None:
        return "NULL"
    return "'" + value.replace("'", "''") + "'"


def sql_bool(value: bool) -> str:
    return "TRUE" if value else "FALSE"


def sql_string_array(values: Sequence[str]) -> str:
    if not values:
        return "CAST(array() AS ARRAY<STRING>)"
    return "array(" + ", ".join(sql_string(value) for value in values) + ")"


def json_or_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return value
