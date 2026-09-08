"""Unity Catalog repositories and server-side resource resolution."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from errors import AppError, ForbiddenError, NotFoundError, ResourceNotReadyError
from gateway import (
    DatabricksGateway,
    JobSubmissionRejectedError,
    json_or_value,
    sql_bool,
    sql_string,
    sql_string_array,
)
from schemas import DocumentMetadata, EvaluationCaseCreate, EvaluationRequest, PreparationRequest
from settings import IndexProfile, Settings, SettingsError


ROLE_LEVEL = {"VIEWER": 1, "EDITOR": 2, "OWNER": 3}
SAFE_MODEL_KEY = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
SAFE_VARIANT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
SAFE_TRACE_ID = re.compile(r"^tr-[0-9a-f]{32}$")
CHAT_MESSAGE_NAMESPACE = uuid.UUID("f57eb6ca-f4f8-4ef2-8727-35ff89bbac40")
EVALUATION_RUN_NAMESPACE = uuid.UUID("9fbe315e-3d32-4d4f-82b1-4f7b1079d2db")
CHAT_TERMINAL_STATUSES = frozenset({"COMPLETED", "CANCELED", "ERROR"})
CHAT_ACTIVE_STATUSES = frozenset({"QUEUED", "STREAMING", "CANCEL_REQUESTED"})
CHAT_RUN_STALE_MINUTES = 30
CHAT_RUN_STALE_ERROR = (
    "回答生成が30分以内に完了しなかったため、自動的に終了しました。"
)
PREP_ACTIVE_STATUSES = frozenset(
    {"QUEUED", "PENDING", "PREPARING", "RUNNING", "TERMINATING", "CANCEL_REQUESTED"}
)
PREP_ACTIVE_STATUS_SQL = ", ".join(sql_string(value) for value in sorted(PREP_ACTIVE_STATUSES))
EVALUATION_ACTIVE_STATUSES = frozenset({"QUEUED", "RUNNING", "CANCEL_REQUESTED"})
EVALUATION_ACTIVE_STATUS_SQL = ", ".join(
    sql_string(value) for value in sorted(EVALUATION_ACTIVE_STATUSES)
)
PREP_SUBMISSION_RETRY_BASE_SECONDS = 30.0
PREP_SUBMISSION_RETRY_MAX_SECONDS = 300.0
PREP_SUBMISSION_INFLIGHT_RETRY_MS = 1000
EVALUATION_SUBMISSION_RETRY_BASE_SECONDS = 30.0
EVALUATION_SUBMISSION_RETRY_MAX_SECONDS = 300.0
EVALUATION_SUBMISSION_INFLIGHT_RETRY_MS = 1000
EVALUATION_SUBMISSION_REJECTED_MESSAGE = (
    "Lakeflow Jobを開始できませんでした。Job設定と権限を確認してください。"
)
PREP_RUN_COLUMNS = """
    prep_run_id, project_id, run_type, target_variant_id, status, current_step,
    completed_steps, total_steps, error_message, config_hash, job_run_id,
    CAST(created_at AS STRING) AS created_at,
    CAST(started_at AS STRING) AS started_at,
    CAST(completed_at AS STRING) AS completed_at
"""
DOCUMENT_CACHE_TTL_SECONDS = 300.0
DOCUMENT_CACHE_MAX_ITEMS = 4
DOCUMENT_CACHE_MAX_BYTES = 256 * 1024 * 1024
SUMMARY_PROMPT_VERSION = "document-summary-ja-v2-two-pass"
NORMALIZED_SUMMARY_PROMPT_VERSION = (
    "document-summary-ja-v2-two-pass+deterministic-boundary-v1"
)


def _overall_evaluation_status(statuses: set[str]) -> str:
    normalized = {str(value).upper() for value in statuses}
    if "CANCEL_REQUESTED" in normalized:
        return "CANCEL_REQUESTED"
    if normalized & {"RUNNING", "QUEUED"}:
        return "RUNNING"
    if normalized == {"SUCCEEDED"}:
        return "SUCCEEDED"
    if normalized == {"CANCELED"}:
        return "CANCELED"
    if "CANCELED" in normalized and "SUCCEEDED" in normalized:
        return "PARTIAL"
    if "FAILED" in normalized and "SUCCEEDED" in normalized:
        return "PARTIAL"
    if "FAILED" in normalized:
        return "FAILED"
    if "PARTIAL" in normalized:
        return "PARTIAL"
    return next(iter(normalized), "UNKNOWN")


@dataclass(frozen=True, slots=True)
class ProjectAccess:
    project_id: str
    role: str
    status: str
    name: str


class Repository:
    def __init__(self, settings: Settings, gateway: DatabricksGateway) -> None:
        # ``Settings.from_env`` validates production configuration already, but
        # injected settings (tests/local preview) must follow the same fail-closed
        # resource-name and existing-Index allow-list contract.
        settings.validate_names()
        self.settings = settings
        self.gateway = gateway
        # A BUILD_VARIANT row is the durable idempotency record. These maps only
        # avoid unnecessary API calls within one App process; correctness across
        # processes comes from the prep_run_id-derived Jobs idempotency token.
        self._prep_submission_lock = threading.Lock()
        self._prep_submission_inflight: set[str] = set()
        self._prep_submission_retries: dict[str, tuple[int, float]] = {}
        self._prep_submission_claims: dict[str, int] = {}
        # Evaluation rows use eval_run_id as the durable Jobs idempotency
        # token.  These process-local values only reduce duplicate API/SQL
        # calls; recovery after an App restart remains safe because run_now
        # receives that same token again.
        self._evaluation_submission_lock = threading.Lock()
        self._evaluation_submission_inflight: set[str] = set()
        self._evaluation_submission_retries: dict[str, tuple[int, float]] = {}
        self._evaluation_submission_claims: dict[str, int] = {}
        self._monotonic = time.monotonic
        # Delta tables do not enforce a UNIQUE constraint on sequence_no.  The
        # durable MERGE statements below are the cross-process idempotency
        # guard; this lock also prevents avoidable write conflicts when one App
        # process receives multiple Enter/retry requests at the same instant.
        self._chat_creation_lock = threading.Lock()
        # PDF objects are immutable because their path includes a generated
        # document UUID. Keeping a small process-local cache avoids a SQL
        # Warehouse round trip and a Volume download when a user reopens a PDF.
        self._document_cache_lock = threading.Lock()
        self._document_cache: OrderedDict[
            tuple[str, str], tuple[float, bytes, str]
        ] = OrderedDict()
        self._document_cache_bytes = 0

    def require_config(self) -> None:
        if self.settings.missing_required:
            raise ResourceNotReadyError(
                "Databricksリソースの環境変数を設定すると利用できます。",
                missing=self.settings.missing_required,
            )

    def table(self, name: str) -> str:
        try:
            return self.settings.table_name(name)
        except SettingsError as exc:
            raise ResourceNotReadyError(str(exc)) from exc

    def list_projects(self, principal: str) -> list[dict[str, Any]]:
        self.require_config()
        p = self.table("toyota_rag_projects")
        m = self.table("toyota_rag_project_members")
        rows = self.gateway.query(f"""
            SELECT p.project_id, p.project_name, p.description, p.status,
                   p.active_variant_id, p.active_dataset_version, m.role,
                   CAST(p.updated_at AS STRING) AS updated_at
            FROM {p} p
            INNER JOIN {m} m ON p.project_id = m.project_id
            WHERE m.principal = {sql_string(principal)}
              AND p.status NOT IN ('ARCHIVED', 'DELETING')
            ORDER BY p.updated_at DESC
            LIMIT 200
        """)
        return [
            {
                "project_id": row["project_id"],
                "name": row["project_name"],
                "description": row.get("description") or "",
                "status": row["status"],
                "role": row["role"],
                "active_variant_id": row.get("active_variant_id"),
                "dataset_version": row.get("active_dataset_version"),
                "updated_at": row.get("updated_at"),
            }
            for row in rows
        ]

    def create_project(self, principal: str, name: str, description: str) -> dict[str, Any]:
        self.require_config()
        project_id = str(uuid.uuid4())
        p = self.table("toyota_rag_projects")
        m = self.table("toyota_rag_project_members")
        self.gateway.execute_sql(f"""
            INSERT INTO {p} (
              project_id, project_name, description, status, created_by,
              created_at, updated_at
            ) VALUES (
              {sql_string(project_id)}, {sql_string(name)}, {sql_string(description)},
              'EMPTY', {sql_string(principal)}, current_timestamp(), current_timestamp()
            )
        """)
        try:
            self.gateway.execute_sql(f"""
                INSERT INTO {m} (project_id, principal, role, added_by, added_at)
                VALUES ({sql_string(project_id)}, {sql_string(principal)}, 'OWNER',
                        {sql_string(principal)}, current_timestamp())
            """)
        except Exception:
            # Prevent a partially-created project from appearing as usable.
            try:
                self.gateway.execute_sql(
                    f"UPDATE {p} SET status='ARCHIVED', updated_at=current_timestamp() "
                    f"WHERE project_id={sql_string(project_id)}"
                )
            finally:
                raise
        return {
            "project_id": project_id,
            "name": name,
            "description": description,
            "status": "EMPTY",
            "role": "OWNER",
        }

    def delete_project(self, principal: str, project_id: str) -> dict[str, Any]:
        """Logically delete a Project and stop new Project-scoped work.

        Delta tables and AI Search indexes are shared infrastructure, and
        Lakeflow tasks may still be committing their terminal state. Dropping
        physical tables/indexes synchronously from an App request would be
        partial and irrecoverable. The Project tombstone and membership are
        therefore retained for audit/cleanup automation; all normal APIs deny
        access as soon as the DELETING transition is claimed.
        """

        project_id = require_uuid(project_id, "project_id")
        projects = self.table("toyota_rag_projects")
        members = self.table("toyota_rag_project_members")
        access = self.gateway.query(f"""
            SELECT p.project_id, p.status, p.mutation_token, m.role
            FROM {projects} p
            INNER JOIN {members} m ON p.project_id=m.project_id
            WHERE p.project_id={sql_string(project_id)}
              AND m.principal={sql_string(principal)}
            LIMIT 1
        """)
        if not access:
            raise NotFoundError("Projectが見つからないか、削除権限がありません。")
        if str(access[0].get("role") or "") != "OWNER":
            raise ForbiddenError("Projectを削除できるのはOWNERだけです。")
        current_status = str(access[0].get("status") or "").upper()
        if current_status == "ARCHIVED":
            return self._project_deletion_response(project_id, already_deleted=True)
        if current_status == "DELETING":
            raise AppError(
                "PROJECT_DELETE_IN_PROGRESS",
                "Projectの削除処理が進行中です。完了後にもう一度確認してください。",
                status_code=409,
                retryable=True,
            )
        if access[0].get("mutation_token"):
            raise AppError(
                "PROJECT_UPDATE_IN_PROGRESS",
                "Projectのデータ更新中です。完了後に削除してください。",
                status_code=409,
                retryable=True,
            )

        # Claim deletion first. require_project rejects DELETING, preventing a
        # new upload/chat/evaluation from racing the following transitions.
        # mutation_token IS NULL makes this claim mutually exclusive with a
        # document deletion that won the Project mutation fence concurrently.
        self.gateway.execute_sql(f"""
            UPDATE {projects} SET status='DELETING', updated_at=current_timestamp()
            WHERE project_id={sql_string(project_id)}
              AND mutation_token IS NULL
              AND status NOT IN ('ARCHIVED', 'DELETING')
        """)
        claimed = self.gateway.query(f"""
            SELECT status, mutation_token FROM {projects}
            WHERE project_id={sql_string(project_id)} LIMIT 1
        """)
        if not claimed:
            raise NotFoundError("Projectが見つかりません。")
        claimed_status = str(claimed[0].get("status") or "").upper()
        if claimed_status == "ARCHIVED":
            return self._project_deletion_response(project_id, already_deleted=True)
        if claimed_status != "DELETING" or claimed[0].get("mutation_token"):
            raise AppError(
                "PROJECT_UPDATE_IN_PROGRESS",
                "Projectのデータ更新と削除が競合しました。完了後にもう一度実行してください。",
                status_code=409,
                retryable=True,
            )
        transitions = (
            ("toyota_rag_chat_runs", """
                status='CANCEL_REQUESTED', cancel_requested_at=current_timestamp()
                WHERE project_id={project} AND status IN ('QUEUED', 'STREAMING')
            """),
            ("toyota_rag_chat_sessions", """
                status='DELETED', updated_at=current_timestamp()
                WHERE project_id={project} AND status='ACTIVE'
            """),
            ("toyota_rag_prep_runs", """
                status='CANCEL_REQUESTED', cancel_requested_at=current_timestamp()
                WHERE project_id={project} AND status IN
                  ('QUEUED', 'PENDING', 'PREPARING', 'RUNNING', 'TERMINATING')
            """),
            ("toyota_rag_eval_runs", """
                status='CANCEL_REQUESTED', cancel_requested_at=current_timestamp()
                WHERE project_id={project} AND status IN ('QUEUED', 'RUNNING', 'PARTIAL')
            """),
        )
        for table_name, suffix in transitions:
            self.gateway.execute_sql(
                f"UPDATE {self.table(table_name)} SET "
                + suffix.format(project=sql_string(project_id))
            )
        self.gateway.execute_sql(f"""
            UPDATE {projects} SET status='ARCHIVED', updated_at=current_timestamp()
            WHERE project_id={sql_string(project_id)} AND status='DELETING'
        """)
        self._evict_project_documents(project_id)
        return self._project_deletion_response(project_id, already_deleted=False)

    @staticmethod
    def _project_deletion_response(
        project_id: str, *, already_deleted: bool
    ) -> dict[str, Any]:
        return {
            "project_id": project_id,
            "status": "ARCHIVED",
            "deleted": True,
            "already_deleted": already_deleted,
            "deletion_mode": "LOGICAL",
            "physical_cleanup": "RETAINED_FOR_AUDIT",
            "retained_resources": [
                "Unity Catalog Volume files",
                "Delta records and Variant source tables",
                "AI Search indexes",
                "Project membership tombstone",
            ],
        }

    def require_project(self, principal: str, project_id: str, minimum_role: str = "VIEWER") -> ProjectAccess:
        project_id = require_uuid(project_id, "project_id")
        p = self.table("toyota_rag_projects")
        m = self.table("toyota_rag_project_members")
        rows = self.gateway.query(f"""
            SELECT p.project_id, p.project_name, p.status, p.mutation_token, m.role
            FROM {p} p INNER JOIN {m} m ON p.project_id=m.project_id
            WHERE p.project_id={sql_string(project_id)}
              AND m.principal={sql_string(principal)}
            LIMIT 1
        """)
        if not rows:
            raise NotFoundError("Projectが見つからないか、閲覧権限がありません。")
        row = rows[0]
        role = str(row["role"])
        if ROLE_LEVEL.get(role, 0) < ROLE_LEVEL[minimum_role]:
            raise ForbiddenError()
        if row["status"] in {"ARCHIVED", "DELETING"}:
            raise ForbiddenError("削除済みまたは削除中のProjectは操作できません。")
        if row.get("mutation_token"):
            raise AppError(
                "PROJECT_UPDATE_IN_PROGRESS",
                "Projectのデータ更新中です。完了後にもう一度実行してください。",
                status_code=409,
                retryable=True,
            )
        return ProjectAccess(project_id, role, str(row["status"]), str(row["project_name"]))

    def list_model_options(self, capability: str) -> list[dict[str, Any]]:
        if capability not in {"embedding", "chat", "chat_tool_calling", "judge"}:
            raise ValueError("unsupported capability")
        table = self.table("toyota_rag_model_catalog")
        defaults = self.table("toyota_rag_model_defaults")
        policy_rows = self.gateway.query(f"""
            SELECT preferred_model_key, fallback_policy
            FROM {defaults}
            WHERE capability={sql_string(capability)}
            LIMIT 1
        """)
        preferred_key = (
            str(policy_rows[0].get("preferred_model_key") or "")
            if policy_rows
            else ""
        )
        capability_predicate = (
            "(array_contains(capabilities, 'chat_tool_calling') "
            "OR array_contains(capabilities, 'tool_calling'))"
            if capability == "chat_tool_calling"
            else (
                "(array_contains(capabilities, 'judge') "
                "OR array_contains(capabilities, 'chat'))"
                if capability == "judge"
                else f"array_contains(capabilities, {sql_string(capability)})"
            )
        )
        rows = self.gateway.query(f"""
            SELECT model_key, display_name, capabilities, endpoint_state,
                   embedding_dimension, max_context_tokens, max_output_tokens,
                   region_available, selectable, unavailable_reason,
                   CAST(verified_at AS STRING) AS verified_at
            FROM {table}
            WHERE target_kind='FMAPI_ENDPOINT'
              AND {capability_predicate}
            ORDER BY display_name, model_key
            LIMIT 500
        """)
        items: list[dict[str, Any]] = []
        for row in rows:
            caps = json_or_value(row.get("capabilities"), [])
            model_key = str(row["model_key"])
            items.append({
                "model_key": model_key,
                "display_name": row["display_name"],
                "capabilities": caps if isinstance(caps, list) else [],
                "state": row.get("endpoint_state"),
                "dimension": _int_or_none(row.get("embedding_dimension")),
                "max_context_tokens": _int_or_none(row.get("max_context_tokens")),
                "max_output_tokens": _int_or_none(row.get("max_output_tokens")),
                "region_available": _bool(row.get("region_available")),
                "selectable": _bool(row.get("selectable")),
                "unavailable_reason": row.get("unavailable_reason"),
                "is_recommended": False,
                "is_default": False,
                "verified_at": row.get("verified_at"),
            })
        eligible = [
            item
            for item in items
            if item["selectable"]
            and item["region_available"]
            and str(item.get("state") or "").upper() == "READY"
        ]
        eligible.sort(
            key=lambda item: (
                str(item.get("display_name") or "").casefold(),
                str(item.get("model_key") or ""),
            )
        )
        selected_default = next(
            (item for item in eligible if item["model_key"] == preferred_key),
            eligible[0] if eligible else None,
        )
        if selected_default is not None:
            selected_default["is_default"] = True
            selected_default["is_recommended"] = True
        items.sort(
            key=lambda item: (
                not item["is_default"],
                not item["selectable"],
                str(item.get("display_name") or "").casefold(),
                str(item.get("model_key") or ""),
            )
        )
        # A configured answer endpoint remains usable while the discovery Job is
        # being prepared, without exposing its physical name to the browser.
        if not items and capability in {"chat", "judge"} and self.settings.default_llm_endpoint:
            items.append({
                "model_key": "default-chat",
                "display_name": "既定の回答LLM",
                "capabilities": ["chat"],
                "state": "CONFIGURED",
                "dimension": None,
                "max_context_tokens": None,
                "max_output_tokens": None,
                "region_available": True,
                "selectable": True,
                "unavailable_reason": None,
                "is_recommended": True,
                "is_default": True,
                "verified_at": None,
            })
        return items

    def resolve_model_target(self, model_key: str, capability: str) -> str:
        if model_key == "default-chat" and capability in {"chat", "judge", "advisor"}:
            if not self.settings.default_llm_endpoint:
                raise ResourceNotReadyError("既定のLLM endpointが設定されていません。")
            return self.settings.default_llm_endpoint
        if not SAFE_MODEL_KEY.fullmatch(model_key):
            raise NotFoundError("選択されたモデルが不正です。")
        table = self.table("toyota_rag_model_catalog")
        lookup_capability = "chat" if capability in {"judge", "advisor"} else capability
        rows = self.gateway.query(f"""
            SELECT target_name FROM {table}
            WHERE model_key={sql_string(model_key)}
              AND target_kind='FMAPI_ENDPOINT'
              AND selectable=TRUE AND region_available=TRUE
              AND array_contains(capabilities, {sql_string(lookup_capability)})
            LIMIT 1
        """)
        if not rows:
            raise NotFoundError("選択したモデルは現在利用できません。モデル一覧を更新してください。")
        return str(rows[0]["target_name"])

    def list_index_profiles(self) -> list[dict[str, Any]]:
        """Expose only administrator-provisioned existing Index profiles."""

        self.require_config()
        return [
            profile.public_dict()
            for profile in self.settings.resolved_index_profiles
        ]

    def resolve_index_profile(
        self,
        profile_key: str,
        configuration: Any | None = None,
    ) -> IndexProfile:
        """Resolve a browser-selected key without accepting physical names."""

        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", str(profile_key or "")):
            raise AppError(
                "INDEX_PROFILE_NOT_CONFIGURED",
                "選択した設定に対応する既存Indexがありません。管理者に準備を依頼してください。",
                status_code=422,
            )
        profile = next(
            (
                item
                for item in self.settings.resolved_index_profiles
                if item.profile_key == profile_key
            ),
            None,
        )
        if profile is None:
            raise AppError(
                "INDEX_PROFILE_NOT_CONFIGURED",
                "選択した設定に対応する既存Indexがありません。管理者に準備を依頼してください。",
                status_code=422,
            )
        if configuration is not None:
            if hasattr(configuration, "model_dump"):
                actual = configuration.model_dump(mode="json")
            elif isinstance(configuration, dict):
                actual = dict(configuration)
            else:
                actual = None
            if actual != profile.configuration:
                raise AppError(
                    "INDEX_PROFILE_MISMATCH",
                    "選択内容と既存Indexの設定が一致しません。画面を再読み込みしてください。",
                    status_code=422,
                )
        return profile

    def _profile_for_resource_pair(
        self,
        source_table: str,
        index_name: str,
    ) -> IndexProfile:
        profile = next(
            (
                item
                for item in self.settings.resolved_index_profiles
                if item.source_table == str(source_table)
                and item.index_name == str(index_name)
            ),
            None,
        )
        if profile is None:
            raise ResourceNotReadyError(
                "Index Variantが管理者の既存Index許可リスト外です。RAG_INDEX_PROFILES_JSONを確認してください。"
            )
        return profile

    def delete_document(
        self,
        principal: str,
        project_id: str,
        document_id: str,
    ) -> dict[str, Any]:
        """Logically delete one PDF and queue immutable replacement Variants.

        The original Volume object, parsed row, chat citations, evaluation
        cases, and evaluation results remain unchanged for auditability.  Any
        registered Variant whose source corpus contained the PDF is fenced out
        of normal list/resolve operations before its successor is submitted.
        """

        project_id = require_uuid(project_id, "project_id")
        document_id = require_uuid(document_id, "document_id")
        projects = self.table("toyota_rag_projects")
        members = self.table("toyota_rag_project_members")
        registry = self.table("toyota_document_registry")
        variants = self.table("toyota_index_variants")
        mutation_token = str(uuid.uuid4())

        access_rows = self.gateway.query(f"""
            SELECT p.status AS project_status, p.active_variant_id,
                   p.mutation_token, p.mutation_type, p.mutation_target_id,
                   m.role, d.document_id, d.processing_status,
                   coalesce(d.lifecycle_status, 'ACTIVE') AS lifecycle_status,
                   d.deletion_request_id, CAST(d.deleted_at AS STRING) AS deleted_at
            FROM {projects} p
            INNER JOIN {members} m ON p.project_id=m.project_id
            LEFT JOIN {registry} d
              ON p.project_id=d.project_id
             AND d.document_id={sql_string(document_id)}
            WHERE p.project_id={sql_string(project_id)}
              AND m.principal={sql_string(principal)}
            LIMIT 1
        """)
        if not access_rows or not access_rows[0].get("document_id"):
            raise NotFoundError("PDFが見つからないか、削除権限がありません。")
        access = access_rows[0]
        if ROLE_LEVEL.get(str(access.get("role") or ""), 0) < ROLE_LEVEL["EDITOR"]:
            raise ForbiddenError("PDFを削除するにはEDITOR以上の権限が必要です。")
        if str(access.get("project_status") or "").upper() in {"ARCHIVED", "DELETING"}:
            raise ForbiddenError("削除済みまたは削除中のProjectは操作できません。")

        lifecycle = str(access.get("lifecycle_status") or "ACTIVE").upper()
        if lifecycle == "DELETED":
            self._evict_document(project_id, document_id)
            return self._existing_document_deletion_response(
                project_id,
                document_id,
                already_deleted=True,
                deletion_status="DELETED",
            )
        resuming_deletion = lifecycle == "DELETING"
        if access.get("mutation_token"):
            same_target = (
                str(access.get("mutation_type") or "") == "DOCUMENT_DELETE"
                and str(access.get("mutation_target_id") or "") == document_id
            )
            if same_target:
                return self._existing_document_deletion_response(
                    project_id,
                    document_id,
                    already_deleted=False,
                    deletion_status="DELETING",
                )
            raise AppError(
                "PROJECT_UPDATE_IN_PROGRESS",
                "Projectの別のデータ更新が進行中です。完了後にもう一度実行してください。",
                status_code=409,
                retryable=True,
            )

        # The Project-scoped mutation fence closes the race with new upload,
        # build, chat, and evaluation API calls.  It is released before this
        # request returns; replacement Jobs never hold the UI lock.
        self.gateway.execute_sql(f"""
            UPDATE {projects}
            SET mutation_token={sql_string(mutation_token)},
                mutation_type='DOCUMENT_DELETE',
                mutation_target_id={sql_string(document_id)},
                mutation_started_at=current_timestamp(),
                updated_at=current_timestamp()
            WHERE project_id={sql_string(project_id)}
              AND mutation_token IS NULL
              AND status NOT IN ('ARCHIVED', 'DELETING')
        """)
        claim_rows = self.gateway.query(f"""
            SELECT mutation_token FROM {projects}
            WHERE project_id={sql_string(project_id)} LIMIT 1
        """)
        if not claim_rows or str(claim_rows[0].get("mutation_token") or "") != mutation_token:
            raise AppError(
                "PROJECT_UPDATE_IN_PROGRESS",
                "Projectの別のデータ更新が進行中です。完了後にもう一度実行してください。",
                status_code=409,
                retryable=True,
            )

        deletion_request_id = (
            str(access.get("deletion_request_id") or "") or str(uuid.uuid4())
        )
        try:
            conflicts = self._document_delete_conflicts(project_id)
            # A retry starts only after the previous synchronous request has
            # released the Project mutation fence.  The document reaches
            # DELETING only after every replacement control row has been
            # created, so active preparation runs at this point are precisely
            # the already-submitted successors.  The Variant mapping is also
            # durable before DELETING, which lets us distinguish those runs
            # from unrelated preparation work started after an earlier error.
            # Chat, evaluation, and unrelated preparation work are rejected.
            if resuming_deletion:
                resume_state = self._existing_document_deletion_response(
                    project_id,
                    document_id,
                    already_deleted=False,
                    deletion_status="DELETING",
                )
                deletion_prep_run_ids = {
                    str(item.get("preparation_run_id") or "")
                    for item in resume_state.get("rebuilds", [])
                    if item.get("preparation_run_id")
                }
                conflicts = [
                    item
                    for item in conflicts
                    if not (
                        item.get("type") == "PREPARATION"
                        and str(item.get("id") or "") in deletion_prep_run_ids
                    )
                ]
            if conflicts:
                raise AppError(
                    "DOCUMENT_DELETE_CONFLICT",
                    "実行中の処理があります。完了または停止後にPDFを削除してください。",
                    status_code=409,
                    retryable=True,
                    details={"conflicts": conflicts, "retry_after_ms": 2000},
                )

            active_count_rows = self.gateway.query(f"""
                SELECT count(*) AS active_document_count
                FROM {registry}
                WHERE project_id={sql_string(project_id)}
                  AND document_id<>{sql_string(document_id)}
                  AND coalesce(lifecycle_status, 'ACTIVE')='ACTIVE'
            """)
            remaining_active_document_count = (
                _int_or_none(active_count_rows[0].get("active_document_count"))
                if active_count_rows
                else 0
            ) or 0
            corpus_empty = remaining_active_document_count == 0

            buildable_rows = self.gateway.query(f"""
                SELECT document_id FROM {registry}
                WHERE project_id={sql_string(project_id)}
                  AND document_id<>{sql_string(document_id)}
                  AND processing_status IN ('PARSED', 'READY')
                  AND coalesce(lifecycle_status, 'ACTIVE')='ACTIVE'
                ORDER BY document_id
                LIMIT 101
            """)
            remaining_buildable_document_ids = [
                require_uuid(str(row.get("document_id") or ""), "document_id")
                for row in buildable_rows
            ]
            if len(remaining_buildable_document_ids) > 100:
                raise AppError(
                    "PROJECT_CORPUS_TOO_LARGE",
                    "自動再構築できるPDFは100件までです。管理者へ連絡してください。",
                    status_code=409,
                )

            active_variant_id = str(access.get("active_variant_id") or "") or None
            impacted = self._document_delete_variant_plans(
                project_id,
                document_id,
                active_variant_id=active_variant_id,
            )
            # With no remaining corpus every READY Variant must be fenced,
            # including a legacy Variant whose lineage cannot be inspected.
            if corpus_empty:
                impacted = self._all_ready_variant_plans(project_id)

            prepared: list[tuple[dict[str, Any], PreparationRequest, bool]] = []
            if remaining_buildable_document_ids:
                remaining_set = set(remaining_buildable_document_ids)
                for plan in impacted:
                    variant_remaining_ids = sorted(
                        remaining_set.intersection(
                            set(plan.get("_source_document_ids") or [])
                        )
                    )
                    # A Variant built only from the deleted PDF has no valid
                    # non-empty successor. It is superseded below without
                    # silently broadening its corpus to unrelated Project PDFs.
                    if not variant_remaining_ids:
                        continue
                    request = self._replacement_preparation_request(
                        plan,
                        variant_remaining_ids,
                    )
                    self.resolve_model_target(
                        request.configuration.embedding_model_key,
                        "embedding",
                    )
                    prepared.append(
                        (
                            plan,
                            request,
                            str(plan["variant_id"]) == active_variant_id,
                        )
                    )
                if prepared and not self.settings.prep_job_id:
                    raise ResourceNotReadyError(
                        "PDF削除後の再構築Jobが未設定です。AppへPREP_JOB_IDを追加してください。",
                        missing=["PREP_JOB_ID"],
                    )

            impacted_ids = [str(plan["variant_id"]) for plan in impacted]
            # Queue every successor before changing any searchable state. If a
            # SQL/API call fails here, the PDF and old Variants stay usable and
            # the operation can be retried after the queued Job completes.
            rebuilds: list[dict[str, Any]] = []
            for plan, request, activate_on_success in prepared:
                replacement = self.create_preparation_run(
                    project_id,
                    principal,
                    request,
                    activate_on_success=activate_on_success,
                )
                replacement_variant_id = str(replacement["target_variant_id"])
                rebuilds.append({
                    "source_variant_id": str(plan["variant_id"]),
                    "replacement_variant_id": replacement_variant_id,
                    "index_profile_key": request.index_profile_key,
                    "preparation_run_id": str(replacement["prep_run_id"]),
                    "status": str(replacement.get("status") or "QUEUED"),
                    "status_url": replacement.get("status_url"),
                    "activate_on_success": activate_on_success,
                })

            replacements_by_source = {
                str(item["source_variant_id"]): str(item["replacement_variant_id"])
                for item in rebuilds
            }
            if impacted_ids:
                # One Delta UPDATE fences the whole affected Variant set
                # atomically.  A per-Variant loop could otherwise leave half
                # the old indexes searchable when the Warehouse call fails.
                replacement_cases = "\n".join(
                    "WHEN "
                    + sql_string(source_variant_id)
                    + " THEN "
                    + (
                        sql_string(replacements_by_source[source_variant_id])
                        if source_variant_id in replacements_by_source
                        else "NULL"
                    )
                    for source_variant_id in impacted_ids
                )
                self.gateway.execute_sql(f"""
                    UPDATE {variants}
                    SET lifecycle_status='SUPERSEDED',
                        superseded_by_variant_id=CASE variant_id
                          {replacement_cases}
                          ELSE superseded_by_variant_id
                        END,
                        superseded_by_deletion_request_id={sql_string(deletion_request_id)},
                        superseded_reason={sql_string('DOCUMENT_DELETED:' + document_id)},
                        superseded_at=current_timestamp()
                    WHERE project_id={sql_string(project_id)}
                      AND variant_id IN (
                        {', '.join(sql_string(item) for item in impacted_ids)}
                      )
                      AND coalesce(lifecycle_status, 'READY')='READY'
                """)

            # DELETING is a durable recovery point.  It is written only after
            # every successor control row exists and the affected Variants are
            # atomically mapped to those successors.  A retry can therefore
            # identify its own preparation runs without ignoring unrelated
            # Project work.  If this UPDATE itself fails, the PDF remains
            # visible as ACTIVE and the same DELETE can be submitted again.
            self.gateway.execute_sql(f"""
                UPDATE {registry}
                SET lifecycle_status='DELETING',
                    deletion_request_id=coalesce(
                      deletion_request_id, {sql_string(deletion_request_id)}
                    ),
                    processing_message='検索対象から削除中（後継Variantを同期中）'
                WHERE project_id={sql_string(project_id)}
                  AND document_id={sql_string(document_id)}
                  AND (
                    coalesce(lifecycle_status, 'ACTIVE')='ACTIVE'
                    OR (
                      lifecycle_status='DELETING'
                      AND (
                        deletion_request_id IS NULL
                        OR deletion_request_id={sql_string(deletion_request_id)}
                      )
                    )
                  )
            """)

            # Include Variants fenced by a previous attempt.  This is required
            # when an exception occurred after the atomic Variant UPDATE but
            # before the Project pointer or document tombstone was finalized.
            existing = self._existing_document_deletion_response(
                project_id,
                document_id,
                already_deleted=False,
                deletion_status="DELETING",
            )
            impacted_ids = list(dict.fromkeys([
                *existing.get("impacted_variant_ids", []),
                *impacted_ids,
            ]))
            rebuilds_by_source = {
                str(item["source_variant_id"]): item for item in rebuilds
            }
            # Prefer the just-read durable row over the response captured when
            # the Job was submitted.  A very fast successor can already be
            # SUCCEEDED or FAILED before this synchronous delete reaches the
            # Project-state transition.
            rebuilds_by_source.update({
                str(item["source_variant_id"]): item
                for item in existing.get("rebuilds", [])
            })
            rebuilds = [
                rebuilds_by_source[source_variant_id]
                for source_variant_id in impacted_ids
                if source_variant_id in rebuilds_by_source
            ]

            active_was_impacted = bool(
                active_variant_id and active_variant_id in set(impacted_ids)
            )
            active_has_successor = any(
                item["activate_on_success"]
                and str(item.get("status") or "").upper()
                not in {"FAILED", "CANCELED", "CANCELLED", "ERROR"}
                for item in rebuilds
            )
            if corpus_empty:
                self.gateway.execute_sql(f"""
                    UPDATE {projects} SET status='EMPTY', active_variant_id=NULL,
                      updated_at=current_timestamp()
                    WHERE project_id={sql_string(project_id)}
                      AND mutation_token={sql_string(mutation_token)}
                      AND status NOT IN ('ARCHIVED', 'DELETING')
                """)
            elif active_was_impacted:
                next_status = "REBUILDING" if active_has_successor else "NEEDS_BUILD"
                self.gateway.execute_sql(f"""
                    UPDATE {projects}
                    SET status=CASE
                          WHEN active_variant_id={sql_string(active_variant_id)}
                            THEN {sql_string(next_status)}
                          ELSE status
                        END,
                        active_variant_id=CASE
                          WHEN active_variant_id={sql_string(active_variant_id)}
                            THEN NULL
                          ELSE active_variant_id
                        END,
                        updated_at=current_timestamp()
                    WHERE project_id={sql_string(project_id)}
                      AND mutation_token={sql_string(mutation_token)}
                      AND status NOT IN ('ARCHIVED', 'DELETING')
                """)

            self.gateway.execute_sql(f"""
                UPDATE {registry}
                SET lifecycle_status='DELETED', processing_status='DELETED',
                    processing_message='検索対象から削除済み（原本と履歴は監査用に保持）',
                    deletion_request_id={sql_string(deletion_request_id)},
                    deleted_by={sql_string(principal)},
                    deleted_at=current_timestamp()
                WHERE project_id={sql_string(project_id)}
                  AND document_id={sql_string(document_id)}
                  AND lifecycle_status='DELETING'
                  AND deletion_request_id={sql_string(deletion_request_id)}
            """)
            self._evict_document(project_id, document_id)
            successor_for_active = (
                next(
                    (
                        item["replacement_variant_id"]
                        for item in rebuilds
                        if item["activate_on_success"]
                    ),
                    None,
                )
                if active_was_impacted
                else active_variant_id
            )
            return {
                "document_id": document_id,
                "status": "DELETED",
                "deletion_status": "DELETED",
                "deleted": True,
                "already_deleted": False,
                "deletion_mode": "LOGICAL",
                "affected_variant_count": len(impacted_ids),
                "impacted_variant_ids": impacted_ids,
                "rebuilds": rebuilds,
                "remaining_document_count": remaining_active_document_count,
                "corpus_empty": corpus_empty,
                "active_variant_id": successor_for_active,
                "physical_file_retained": True,
                "retained_history": {
                    "chat_citations": True,
                    "evaluation_results": True,
                },
                "message": (
                    "PDFを検索対象から削除しました。Projectに検索対象のPDFはありません。"
                    if corpus_empty
                    else (
                        "PDFを検索対象から削除し、後継Index Variantの再構築を開始しました。"
                        if rebuilds
                        else "PDFを検索対象から削除しました。空になったVariantに後継はありません。"
                    )
                ),
            }
        finally:
            # Do not hold the UI fence while Lakeflow Jobs run.  A guarded
            # release cannot unlock another request's later mutation.
            self.gateway.execute_sql(f"""
                UPDATE {projects}
                SET mutation_token=NULL, mutation_type=NULL,
                    mutation_target_id=NULL, mutation_started_at=NULL,
                    updated_at=current_timestamp()
                WHERE project_id={sql_string(project_id)}
                  AND mutation_token={sql_string(mutation_token)}
            """)

    def _document_delete_conflicts(self, project_id: str) -> list[dict[str, Any]]:
        # App replicas can be restarted while an answer is running.  Without a
        # durable worker lease, those orphan rows otherwise remain STREAMING
        # forever and block every later PDF deletion.  Reconcile only runs far
        # beyond the interactive request budget; fresh work stays protected.
        self._recover_stale_chat_runs(project_id)
        checks = (
            (
                "PREPARATION",
                "toyota_rag_prep_runs",
                "prep_run_id",
                f"status IN ({PREP_ACTIVE_STATUS_SQL})",
            ),
            (
                "CHAT",
                "toyota_rag_chat_runs",
                "request_id",
                "status IN ('QUEUED', 'STREAMING', 'CANCEL_REQUESTED')",
            ),
            (
                "EVALUATION",
                "toyota_rag_eval_runs",
                "eval_run_id",
                "status IN ('QUEUED', 'RUNNING', 'PARTIAL', 'CANCEL_REQUESTED')",
            ),
        )
        conflicts: list[dict[str, Any]] = []
        for conflict_type, table_name, id_column, predicate in checks:
            rows = self.gateway.query(f"""
                SELECT {id_column} AS conflict_id, status
                FROM {self.table(table_name)}
                WHERE project_id={sql_string(project_id)} AND {predicate}
                LIMIT 20
            """)
            conflicts.extend({
                "type": conflict_type,
                "id": str(row.get("conflict_id") or ""),
                "status": str(row.get("status") or "UNKNOWN").upper(),
            } for row in rows)
        return conflicts

    def _recover_stale_chat_runs(self, project_id: str) -> None:
        """Close orphaned chat runs without overwriting a terminal winner.

        Both UPDATE statements are guarded and idempotent.  If completion wins
        the race, the first predicate no longer matches and the completed run
        and message remain untouched.
        """

        project_id = require_uuid(project_id, "project_id")
        runs = self.table("toyota_rag_chat_runs")
        messages = self.table("toyota_rag_chat_messages")
        cutoff = f"current_timestamp() - INTERVAL {CHAT_RUN_STALE_MINUTES} MINUTES"
        self._execute_chat_write(f"""
            UPDATE {runs}
            SET status='ERROR', completed_at=current_timestamp(),
                error_message={sql_string(CHAT_RUN_STALE_ERROR)}
            WHERE project_id={sql_string(project_id)}
              AND status IN ('QUEUED', 'STREAMING', 'CANCEL_REQUESTED')
              AND started_at IS NOT NULL
              AND started_at < {cutoff}
        """)
        self._execute_chat_write(f"""
            UPDATE {messages} AS message
            SET generation_status='ERROR'
            WHERE message.project_id={sql_string(project_id)}
              AND message.role='assistant'
              AND message.generation_status='STREAMING'
              AND EXISTS (
                SELECT 1 FROM {runs} AS run
                WHERE run.project_id=message.project_id
                  AND run.request_id=message.request_id
                  AND run.status='ERROR'
                  AND run.error_message={sql_string(CHAT_RUN_STALE_ERROR)}
              )
        """)

    def _document_delete_variant_plans(
        self,
        project_id: str,
        document_id: str,
        *,
        active_variant_id: str | None,
    ) -> list[dict[str, Any]]:
        rows = self._all_ready_variant_plans(project_id)
        impacted: list[dict[str, Any]] = []
        for row in rows:
            source_ids = self._variant_source_document_ids(project_id, row)
            if document_id in source_ids:
                impacted.append({
                    **row,
                    "_source_document_ids": sorted(source_ids),
                })
                continue
            if str(row.get("variant_id") or "") == active_variant_id and not source_ids:
                # Never leave an active legacy Index searchable when its
                # lineage cannot prove that the deleted PDF is absent.
                raise AppError(
                    "VARIANT_LINEAGE_UNAVAILABLE",
                    "有効なIndex Variantの元PDFを確認できないため、安全に削除できません。",
                    status_code=409,
                )
        return impacted

    def _all_ready_variant_plans(self, project_id: str) -> list[dict[str, Any]]:
        variants = self.table("toyota_index_variants")
        prep = self.table("toyota_rag_prep_runs")
        rows = self.gateway.query(f"""
            WITH latest_prep AS (
              SELECT target_variant_id, document_ids, normalized_config_json,
                     row_number() OVER (
                       PARTITION BY target_variant_id
                       ORDER BY created_at DESC, prep_run_id DESC
                     ) AS row_number
              FROM {prep}
              WHERE project_id={sql_string(project_id)}
                AND run_type='BUILD_VARIANT' AND status='SUCCEEDED'
            )
            SELECT v.variant_id, v.source_table, v.index_name,
                   v.chunk_method, v.chunk_size,
                   v.parent_chunk_size, v.cleaning_enabled,
                   v.semantic_metadata_enabled, v.embedding_model_key,
                   v.chunker_config_json,
                   to_json(v.source_document_ids) AS source_document_ids_json,
                   to_json(p.document_ids) AS prep_document_ids_json,
                   p.normalized_config_json AS prep_config_json
            FROM {variants} v
            LEFT JOIN latest_prep p
              ON v.variant_id=p.target_variant_id AND p.row_number=1
            WHERE v.project_id={sql_string(project_id)}
              AND v.index_name IS NOT NULL
              AND coalesce(v.lifecycle_status, 'READY')='READY'
            ORDER BY v.created_at, v.variant_id
        """)
        for row in rows:
            self._profile_for_resource_pair(
                str(row.get("source_table") or ""),
                str(row.get("index_name") or ""),
            )
        return rows

    def _variant_source_document_ids(
        self,
        project_id: str,
        row: dict[str, Any],
    ) -> set[str]:
        for field in ("source_document_ids_json", "prep_document_ids_json"):
            values = json_or_value(row.get(field), [])
            if isinstance(values, list) and values:
                return {
                    require_uuid(str(value), "document_id") for value in values
                }

        profile = self._profile_for_resource_pair(
            str(row.get("source_table") or ""),
            str(row.get("index_name") or ""),
        )
        safe_table = self.settings.table_name(profile.source_table.split(".")[2])
        source_rows = self.gateway.query(f"""
            SELECT document_id FROM {safe_table}
            WHERE project_id={sql_string(project_id)}
            GROUP BY document_id ORDER BY document_id LIMIT 101
        """)
        if len(source_rows) > 100:
            raise AppError(
                "VARIANT_LINEAGE_UNAVAILABLE",
                "Index Variantの元PDFが上限を超えています。",
                status_code=409,
            )
        return {
            require_uuid(str(item.get("document_id") or ""), "document_id")
            for item in source_rows
        }

    def _replacement_preparation_request(
        self,
        plan: dict[str, Any],
        document_ids: list[str],
    ) -> PreparationRequest:
        stored = json_or_value(plan.get("prep_config_json"), {})
        configuration = stored.get("configuration") if isinstance(stored, dict) else None
        stored_profile_key = (
            stored.get("index_profile_key")
            if isinstance(stored, dict)
            else None
        )
        resource_profile = self._profile_for_resource_pair(
            str(plan.get("source_table") or ""),
            str(plan.get("index_name") or ""),
        )
        if (
            isinstance(stored_profile_key, str)
            and stored_profile_key
            and stored_profile_key != resource_profile.profile_key
        ):
            raise AppError(
                "VARIANT_CONFIG_UNAVAILABLE",
                "既存Index Variantのprofileと物理リソースが一致しないため、PDFを削除できません。",
                status_code=409,
            )
        profile_key = resource_profile.profile_key
        if not isinstance(configuration, dict):
            chunker_config = json_or_value(plan.get("chunker_config_json"), {})
            content_profile = (
                chunker_config.get("content_profile")
                if isinstance(chunker_config, dict)
                else None
            ) or "LAYOUT_PRESERVING"
            configuration = {
                "chunk_method": str(plan.get("chunk_method") or ""),
                "chunk_size_tokens": _int_or_none(plan.get("chunk_size")),
                "parent_chunk_size_tokens": _int_or_none(plan.get("parent_chunk_size")),
                "content_profile": content_profile,
                "cleaning_enabled": _bool(plan.get("cleaning_enabled")),
                "semantic_metadata_enabled": _bool(
                    plan.get("semantic_metadata_enabled")
                ),
                "embedding_model_key": str(plan.get("embedding_model_key") or ""),
            }
        try:
            request = PreparationRequest.model_validate({
                "run_type": "BUILD_VARIANT",
                "document_ids": sorted(document_ids),
                "index_profile_key": profile_key,
                "configuration": configuration,
            })
            self.resolve_index_profile(profile_key, request.configuration)
            return request
        except Exception as exc:
            if isinstance(exc, AppError) and exc.code == "VARIANT_CONFIG_UNAVAILABLE":
                raise
            raise AppError(
                "VARIANT_CONFIG_UNAVAILABLE",
                "既存Index Variantの設定を再現できないため、PDFを削除できません。",
                status_code=409,
            ) from exc

    def _existing_document_deletion_response(
        self,
        project_id: str,
        document_id: str,
        *,
        already_deleted: bool,
        deletion_status: str,
    ) -> dict[str, Any]:
        rows = self.gateway.query(f"""
            WITH latest_rebuild AS (
              SELECT prep_run_id, target_variant_id, status,
                     normalized_config_json,
                     row_number() OVER (
                       PARTITION BY target_variant_id
                       ORDER BY created_at DESC, prep_run_id DESC
                     ) AS row_number
              FROM {self.table('toyota_rag_prep_runs')}
              WHERE project_id={sql_string(project_id)}
                AND run_type='BUILD_VARIANT'
            )
            SELECT v.variant_id, v.superseded_by_variant_id,
                   r.prep_run_id, r.status AS rebuild_status,
                   r.normalized_config_json
            FROM {self.table('toyota_index_variants')} v
            LEFT JOIN latest_rebuild r
              ON v.superseded_by_variant_id=r.target_variant_id
             AND r.row_number=1
            WHERE v.project_id={sql_string(project_id)}
              AND v.superseded_reason={sql_string('DOCUMENT_DELETED:' + document_id)}
            ORDER BY v.variant_id
        """)
        impacted_ids = [str(row["variant_id"]) for row in rows]
        rebuilds = []
        for row in rows:
            replacement_id = row.get("superseded_by_variant_id")
            if not replacement_id:
                continue
            stored = json_or_value(row.get("normalized_config_json"), {})
            activate_on_success = not (
                isinstance(stored, dict)
                and stored.get("activate_on_success") is False
            )
            prep_run_id = str(row.get("prep_run_id") or "") or None
            rebuilds.append({
                "source_variant_id": str(row["variant_id"]),
                "replacement_variant_id": str(replacement_id),
                "index_profile_key": (
                    str(stored.get("index_profile_key") or "")
                    if isinstance(stored, dict)
                    else ""
                ) or None,
                "preparation_run_id": prep_run_id,
                "status": str(row.get("rebuild_status") or "UNKNOWN"),
                "status_url": (
                    f"/api/projects/{project_id}/preparation-runs/{prep_run_id}"
                    if prep_run_id
                    else None
                ),
                "activate_on_success": activate_on_success,
            })
        remaining_rows = self.gateway.query(f"""
            SELECT count(*) AS document_count,
              (SELECT active_variant_id
               FROM {self.table('toyota_rag_projects')}
               WHERE project_id={sql_string(project_id)} LIMIT 1)
                AS active_variant_id
            FROM {self.table('toyota_document_registry')}
            WHERE project_id={sql_string(project_id)}
              AND coalesce(lifecycle_status, 'ACTIVE')='ACTIVE'
        """)
        remaining = (
            _int_or_none(remaining_rows[0].get("document_count"))
            if remaining_rows
            else 0
        ) or 0
        return {
            "document_id": document_id,
            "status": deletion_status,
            "deletion_status": deletion_status,
            "deleted": deletion_status == "DELETED",
            "already_deleted": already_deleted,
            "deletion_mode": "LOGICAL",
            "affected_variant_count": len(impacted_ids),
            "impacted_variant_ids": impacted_ids,
            "rebuilds": rebuilds,
            "remaining_document_count": remaining,
            "corpus_empty": remaining == 0,
            "active_variant_id": (
                remaining_rows[0].get("active_variant_id")
                if remaining_rows
                else None
            ),
            "physical_file_retained": True,
            "retained_history": {
                "chat_citations": True,
                "evaluation_results": True,
            },
            "message": (
                "PDFの削除処理が進行中です。"
                if deletion_status == "DELETING"
                else "PDFは検索対象から削除済みです。"
            ),
        }

    def list_documents(self, project_id: str) -> list[dict[str, Any]]:
        table = self.table("toyota_document_registry")
        rows = self.gateway.query(f"""
            SELECT document_id, title, summary, summary_source, summary_model_key,
                   page_count, model, model_year, document_type, vehicle_category,
                   category, to_json(tags) AS tags, CAST(document_date AS STRING) AS document_date,
                   source, metadata_json,
                   language,
                   CASE WHEN lifecycle_status='DELETING' THEN 'DELETING'
                        ELSE processing_status END AS processing_status,
                   processing_message,
                   coalesce(lifecycle_status, 'ACTIVE') AS lifecycle_status,
                   deletion_request_id,
                   CAST(uploaded_at AS STRING) AS uploaded_at
            FROM {table}
            WHERE project_id={sql_string(project_id)}
              AND coalesce(lifecycle_status, 'ACTIVE') IN ('ACTIVE', 'DELETING')
            ORDER BY uploaded_at DESC
            LIMIT 500
        """)
        items: list[dict[str, Any]] = []
        for row in rows:
            stored_metadata = _stored_document_metadata(row.get("metadata_json"))
            custom_metadata = stored_metadata.get("custom")
            if not isinstance(custom_metadata, dict):
                custom_metadata = {}
            tags = json_or_value(row.get("tags"), [])
            items.append({
                **row,
                "model_year": _int_or_none(row.get("model_year")),
                "page_count": _int_or_none(row.get("page_count")),
                "tags": [str(item) for item in tags] if isinstance(tags, list) else [],
                "custom_metadata": {
                    str(key): str(value) for key, value in custom_metadata.items()
                    if isinstance(key, str) and isinstance(value, str)
                },
                "href": f"/projects/{project_id}/catalog/{row['document_id']}",
            })
        return items

    def register_document(
        self,
        *,
        project_id: str,
        principal: str,
        filename: str,
        metadata: DocumentMetadata,
        data: bytes,
    ) -> dict[str, Any]:
        if metadata.model:
            self._validate_vehicle_metadata(metadata)
        digest = hashlib.sha256(data).hexdigest()
        registry = self.table("toyota_document_registry")
        duplicates = self.gateway.query(f"""
            SELECT document_id FROM {registry}
            WHERE project_id={sql_string(project_id)} AND sha256={sql_string(digest)}
              AND coalesce(lifecycle_status, 'ACTIVE')='ACTIVE'
            LIMIT 1
        """)
        if duplicates:
            raise ResourceNotReadyError("同じPDFはこのProjectに登録済みです。")

        document_id = str(uuid.uuid4())
        parse_run_id = str(uuid.uuid4())
        path = f"{self.settings.volume_path}/projects/{project_id}/source_pdfs/{document_id}.pdf"
        title = metadata.title or _title_from_filename(filename)
        category = metadata.category or metadata.document_type
        document_date = metadata.document_date.isoformat() if metadata.document_date else None
        initial_summary = metadata.summary or _fallback_document_summary(title)
        summary_source = "MANUAL" if metadata.summary else "FILENAME_FALLBACK"
        summary_status = "READY" if metadata.summary else "PENDING_AI"
        metadata_json = _canonical_document_metadata(
            metadata,
            title=title,
            category=category,
        )
        self.gateway.upload_volume_file(path, data)
        self.gateway.execute_sql(f"""
            INSERT INTO {registry} (
              document_id, project_id, doc_uri, original_filename, title,
              summary, summary_source, summary_prompt_version, summary_status,
              model, model_year, document_type, vehicle_category, language,
              category, tags, document_date, source, metadata_json,
              sha256, source_size_bytes, uploaded_by, uploaded_at,
              processing_status, processing_message, lifecycle_status
            ) VALUES (
              {sql_string(document_id)}, {sql_string(project_id)}, {sql_string(path)},
              {sql_string(filename)}, {sql_string(title)},
              {sql_string(initial_summary)}, {sql_string(summary_source)},
              {sql_string(SUMMARY_PROMPT_VERSION)}, {sql_string(summary_status)},
              {sql_string(metadata.model)}, {metadata.model_year if metadata.model_year is not None else 'NULL'},
              {sql_string(metadata.document_type)}, {sql_string(metadata.vehicle_category)},
              'und', {sql_string(category)}, {sql_string_array(metadata.tags)},
              {f"CAST({sql_string(document_date)} AS DATE)" if document_date else 'NULL'},
              {sql_string(metadata.source)}, {sql_string(metadata_json)},
              {sql_string(digest)}, {len(data)}, {sql_string(principal)},
              current_timestamp(), 'UPLOADED', 'Document Parsingの開始待ち', 'ACTIVE'
            )
        """)
        prep = self.table("toyota_rag_prep_runs")
        parse_config = json.dumps({"parse_schema_version": "2.0"}, sort_keys=True)
        config_hash = hashlib.sha256(parse_config.encode()).hexdigest()
        self.gateway.execute_sql(f"""
            INSERT INTO {prep} (
              prep_run_id, project_id, run_type, document_ids, target_variant_id,
              requested_by, status, normalized_config_json, config_hash,
              current_step, completed_steps, total_steps, created_at
            ) VALUES (
              {sql_string(parse_run_id)}, {sql_string(project_id)}, 'PARSE_ONLY',
              array({sql_string(document_id)}), NULL, {sql_string(principal)}, 'QUEUED',
              {sql_string(parse_config)}, {sql_string(config_hash)}, 'upload', 1, 3,
              current_timestamp()
            )
        """)
        self._cache_document_content(project_id, document_id, data, filename)
        return {
            "document_id": document_id,
            "parse_run_id": parse_run_id,
            "status": "QUEUED",
            "status_url": f"/api/projects/{project_id}/preparation-runs/{parse_run_id}",
            "doc_uri": path,
        }

    def _validate_vehicle_metadata(self, metadata: DocumentMetadata) -> None:
        table = self.table("toyota_vehicle_master")
        rows = self.gateway.query(f"""
            SELECT model, to_json(valid_model_years) AS years, vehicle_category
            FROM {table}
            WHERE active=TRUE AND (
              lower(model)=lower({sql_string(metadata.model)}) OR
              exists(aliases, alias -> lower(alias)=lower({sql_string(metadata.model)}))
            ) LIMIT 1
        """)
        if not rows:
            raise AppError(
                "INVALID_METADATA",
                "車種が車種・年式マスタにありません。管理者へマスタ登録を依頼してください。",
                status_code=422,
            )
        years = {_int_or_none(item) for item in json_or_value(rows[0].get("years"), [])}
        if metadata.model_year is not None and metadata.model_year not in years:
            raise AppError(
                "INVALID_METADATA",
                "選択した車種と年式の組み合わせがマスタにありません。",
                status_code=422,
            )
        expected_category = rows[0].get("vehicle_category")
        if (
            expected_category
            and metadata.vehicle_category is not None
            and metadata.vehicle_category != expected_category
        ):
            raise AppError(
                "INVALID_METADATA",
                "車両カテゴリが車種マスタと一致しません。",
                status_code=422,
            )

    def retry_document_parse(
        self,
        project_id: str,
        document_id: str,
        principal: str,
    ) -> dict[str, Any]:
        """Queue a fresh parse attempt for a document in ERROR state."""
        document_id = require_uuid(document_id, "document_id")
        registry = self.table("toyota_document_registry")
        rows = self.gateway.query(f"""
            SELECT doc_uri, processing_status
            FROM {registry}
            WHERE project_id={sql_string(project_id)}
              AND document_id={sql_string(document_id)}
              AND coalesce(lifecycle_status, 'ACTIVE')='ACTIVE'
            LIMIT 1
        """)
        if not rows:
            raise NotFoundError("再解析するPDFが見つかりません。")
        if str(rows[0].get("processing_status") or "").upper() != "ERROR":
            raise AppError(
                "DOCUMENT_NOT_RETRYABLE",
                "エラーになったPDFだけ再解析できます。",
                status_code=409,
            )

        prep = self.table("toyota_rag_prep_runs")
        # A failed parser may leave an earlier run in a non-terminal state.
        # Fence that stale attempt before creating the replacement run.
        self.gateway.execute_sql(f"""
            UPDATE {prep}
            SET status='CANCELED', error_message='利用者が再解析を開始しました',
                completed_at=current_timestamp()
            WHERE project_id={sql_string(project_id)}
              AND run_type='PARSE_ONLY'
              AND array_contains(document_ids, {sql_string(document_id)})
              AND status IN ({PREP_ACTIVE_STATUS_SQL})
        """)
        parse_run_id = str(uuid.uuid4())
        parse_config = json.dumps({"parse_schema_version": "2.0"}, sort_keys=True)
        config_hash = hashlib.sha256(parse_config.encode()).hexdigest()
        self.gateway.execute_sql(f"""
            INSERT INTO {prep} (
              prep_run_id, project_id, run_type, document_ids, target_variant_id,
              requested_by, status, normalized_config_json, config_hash,
              current_step, completed_steps, total_steps, created_at
            ) VALUES (
              {sql_string(parse_run_id)}, {sql_string(project_id)}, 'PARSE_ONLY',
              array({sql_string(document_id)}), NULL, {sql_string(principal)}, 'QUEUED',
              {sql_string(parse_config)}, {sql_string(config_hash)}, 'upload', 1, 3,
              current_timestamp()
            )
        """)
        self.gateway.execute_sql(f"""
            UPDATE {registry}
            SET processing_status='UPLOADED',
                processing_message='Document Parsingの再開待ち'
            WHERE project_id={sql_string(project_id)}
              AND document_id={sql_string(document_id)}
              AND processing_status='ERROR'
              AND coalesce(lifecycle_status, 'ACTIVE')='ACTIVE'
        """)
        return {
            "document_id": document_id,
            "parse_run_id": parse_run_id,
            "status": "QUEUED",
            "status_url": f"/api/projects/{project_id}/preparation-runs/{parse_run_id}",
            "doc_uri": str(rows[0]["doc_uri"]),
        }

    def build_parse_sql(self, project_id: str, document_id: str, doc_uri: str) -> str:
        """Build the FILE-typed parsing statement required by the guide."""
        require_uuid(project_id, "project_id")
        require_uuid(document_id, "document_id")
        expected = f"{self.settings.volume_path}/projects/{project_id}/source_pdfs/{document_id}.pdf"
        if doc_uri != expected:
            raise ValueError("Document URI does not match the server-generated path")
        parsed = self.table("toyota_parsed_v2")
        registry = self.table("toyota_document_registry")
        image_path = f"{self.settings.volume_path}/projects/{project_id}/page_images/{document_id}/"
        return f"""
            MERGE INTO {parsed} AS target
            USING (
              SELECT r.project_id, r.document_id,
                     regexp_replace(f.source_file.uri, '^dbfs:', '') AS doc_uri,
                     f.modification_time AS source_modified_at,
                     f.size AS source_size_bytes,
                     ai_parse_document(
                       f.source_file,
                       map('version', '2.0',
                           'imageOutputPath', {sql_string(image_path)},
                           'descriptionElementTypes', '*')
                     ) AS parsed,
                     current_timestamp() AS parsed_at
              FROM (
                SELECT path, size, modification_time, file AS source_file
                FROM READ_FILES({sql_string(doc_uri)}, format => 'file')
              ) f
              JOIN {registry} r
                ON regexp_replace(f.source_file.uri, '^dbfs:', '') = r.doc_uri
              WHERE r.project_id={sql_string(project_id)}
                AND r.document_id={sql_string(document_id)}
                AND coalesce(r.lifecycle_status, 'ACTIVE')='ACTIVE'
            ) AS source
            ON target.project_id=source.project_id
               AND target.document_id=source.document_id
            WHEN MATCHED THEN UPDATE SET *
            WHEN NOT MATCHED THEN INSERT *
        """

    def parse_document(self, project_id: str, document_id: str, parse_run_id: str, doc_uri: str) -> None:
        registry = self.table("toyota_document_registry")
        prep = self.table("toyota_rag_prep_runs")
        parsed = self.table("toyota_parsed_v2")
        try:
            self.gateway.execute_sql(f"""
                UPDATE {prep} SET status='RUNNING', current_step='parsing',
                  started_at=current_timestamp(), completed_steps=1
                WHERE project_id={sql_string(project_id)}
                  AND prep_run_id={sql_string(parse_run_id)} AND status='QUEUED'
            """)
            self.gateway.execute_sql(f"""
                UPDATE {registry} SET processing_status='PARSING',
                  processing_message='ai_parse_documentで解析中'
                WHERE project_id={sql_string(project_id)}
                  AND document_id={sql_string(document_id)}
                  AND coalesce(lifecycle_status, 'ACTIVE')='ACTIVE'
            """)
            self.gateway.execute_sql(self.build_parse_sql(project_id, document_id, doc_uri), timeout_seconds=120)
            checks = self.gateway.query(f"""
                SELECT to_json(p.parsed:error_status) AS errors,
                  r.title, r.original_filename, r.summary, r.summary_source,
                  substring(
                    trim(regexp_replace(
                      concat_ws(' ', transform(
                        from_json(
                          to_json(p.parsed:document:elements),
                          'ARRAY<STRUCT<content:STRING,description:STRING>>'
                        ),
                        element -> coalesce(
                          nullif(trim(element.content), ''),
                          nullif(trim(element.description), ''),
                          ''
                        )
                      )),
                      '\\s+', ' '
                    )),
                    1, 6000
                  ) AS parsed_text
                FROM {parsed} p
                INNER JOIN {registry} r
                  ON p.project_id=r.project_id AND p.document_id=r.document_id
                WHERE p.project_id={sql_string(project_id)}
                  AND p.document_id={sql_string(document_id)} LIMIT 1
            """)
            errors = (checks[0].get("errors") if checks else None) or "[]"
            if errors not in {"[]", "null", "{}"}:
                raise RuntimeError("ai_parse_document returned an error status")
            parsed_row = checks[0] if checks else {}
            manual_summary = (
                str(parsed_row.get("summary") or "").strip()
                if str(parsed_row.get("summary_source") or "").upper() == "MANUAL"
                else ""
            )
            generated_summary: str | None = None
            generated_source: str | None = None
            summary_model_key: str | None = None
            if not manual_summary:
                generated_summary, generated_source, summary_model_key = (
                    self.generate_document_summary(
                        title=str(parsed_row.get("title") or _title_from_filename(
                            str(parsed_row.get("original_filename") or "document.pdf")
                        )),
                        filename=str(parsed_row.get("original_filename") or "document.pdf"),
                        parsed_text=str(parsed_row.get("parsed_text") or ""),
                    )
                )
            generated_summary_sql = (
                sql_string(generated_summary) if generated_summary else "NULL"
            )
            generated_source_sql = (
                sql_string(generated_source) if generated_source else "NULL"
            )
            summary_model_sql = (
                sql_string(summary_model_key) if summary_model_key else "NULL"
            )
            self.gateway.execute_sql(f"""
                MERGE INTO {registry} AS target
                USING (
                  SELECT project_id, document_id, source_modified_at, source_size_bytes,
                    size(from_json(to_json(parsed:document:pages), 'ARRAY<VARIANT>')) AS page_count,
                    substring(
                      trim(regexp_replace(
                        concat_ws(' ', transform(
                          from_json(
                            to_json(parsed:document:elements),
                            'ARRAY<STRUCT<content:STRING,description:STRING>>'
                          ),
                          element -> coalesce(
                            nullif(trim(element.content), ''),
                            nullif(trim(element.description), ''),
                            ''
                          )
                        )),
                        '\\s+', ' '
                      )),
                      1, 1200
                    ) AS parsed_summary
                  FROM {parsed}
                  WHERE project_id={sql_string(project_id)}
                    AND document_id={sql_string(document_id)}
                ) AS source
                ON target.project_id=source.project_id
                  AND target.document_id=source.document_id
                WHEN MATCHED AND coalesce(target.lifecycle_status, 'ACTIVE')='ACTIVE'
                  THEN UPDATE SET
                  target.processing_status='PARSED',
                  target.processing_message='解析結果を確認できます',
                  target.source_modified_at=source.source_modified_at,
                  target.source_size_bytes=source.source_size_bytes,
                  target.page_count=source.page_count,
                  target.summary=CASE
                    WHEN target.summary_source='MANUAL' THEN target.summary
                    ELSE coalesce(
                      {generated_summary_sql}, nullif(source.parsed_summary, ''),
                      target.summary
                    )
                  END,
                  target.summary_source=CASE
                    WHEN target.summary_source='MANUAL' THEN target.summary_source
                    WHEN {generated_summary_sql} IS NOT NULL THEN {generated_source_sql}
                    WHEN nullif(source.parsed_summary, '') IS NOT NULL THEN 'PARSED_EXTRACT'
                    ELSE 'FILENAME_FALLBACK'
                  END,
                  target.summary_model_key=CASE
                    WHEN target.summary_source='MANUAL' THEN target.summary_model_key
                    ELSE {summary_model_sql}
                  END,
                  target.summary_prompt_version=CASE
                    WHEN target.summary_source='MANUAL'
                      THEN target.summary_prompt_version
                    WHEN {generated_source_sql}='AI_GENERATED_NORMALIZED'
                      THEN {sql_string(NORMALIZED_SUMMARY_PROMPT_VERSION)}
                    ELSE {sql_string(SUMMARY_PROMPT_VERSION)}
                  END,
                  target.summary_status=CASE
                    WHEN target.summary_source='MANUAL'
                      OR {generated_summary_sql} IS NOT NULL
                      OR nullif(source.parsed_summary, '') IS NOT NULL
                      OR nullif(trim(target.summary), '') IS NOT NULL THEN 'READY'
                    ELSE 'EMPTY'
                  END
            """)
            self.gateway.execute_sql(f"""
                UPDATE {prep} SET status='SUCCEEDED', current_step='summary',
                  completed_steps=3, completed_at=current_timestamp()
                WHERE project_id={sql_string(project_id)}
                  AND prep_run_id={sql_string(parse_run_id)}
            """)
        except Exception:
            # Keep the browser message actionable without persisting credentials or paths.
            try:
                self.gateway.execute_sql(f"""
                    UPDATE {registry} SET processing_status='ERROR',
                      processing_message='Document Parsingに失敗しました。実行ログを確認してください。'
                    WHERE project_id={sql_string(project_id)}
                      AND document_id={sql_string(document_id)}
                      AND coalesce(lifecycle_status, 'ACTIVE')='ACTIVE'
                """)
                self.gateway.execute_sql(f"""
                    UPDATE {prep} SET status='FAILED', current_step='parsing',
                      error_message='Document Parsingに失敗しました',
                      completed_at=current_timestamp()
                    WHERE project_id={sql_string(project_id)}
                      AND prep_run_id={sql_string(parse_run_id)}
                """)
            except Exception:
                # A failed status update must not cause the background task to
                # leak the original SQL/Volume details into an HTTP response.
                pass

    def generate_document_summary(
        self,
        *,
        title: str,
        filename: str,
        parsed_text: str,
    ) -> tuple[str, str, str | None]:
        """Generate a short Japanese catalog summary without blocking upload.

        This method runs in the existing parsing background task. Credentials
        remain inside the Gateway and model failures deliberately degrade to a
        deterministic filename/title summary instead of failing ingestion.
        """

        fallback = _fallback_document_summary(title or _title_from_filename(filename))
        model_key: str | None = None
        model_target: str | None = None
        try:
            catalog = self.table("toyota_rag_model_catalog")
            rows = self.gateway.query(f"""
                SELECT model_key, target_name
                FROM {catalog}
                WHERE target_kind='FMAPI_ENDPOINT'
                  AND selectable=TRUE AND region_available=TRUE
                  AND (
                    array_contains(capabilities, 'chat_tool_calling')
                    OR array_contains(capabilities, 'tool_calling')
                  )
                ORDER BY CASE
                  WHEN target_name={sql_string(self.settings.default_llm_endpoint)} THEN 0
                  ELSE 1 END, display_name
                LIMIT 1
            """)
            if rows and rows[0].get("target_name"):
                model_key = str(rows[0].get("model_key") or "") or None
                model_target = str(rows[0]["target_name"])
            elif self.settings.default_llm_endpoint:
                model_key = "default-chat"
                model_target = self.settings.default_llm_endpoint
            if not model_target:
                return fallback, "FILENAME_FALLBACK", None

            excerpt = re.sub(r"\s+", " ", parsed_text).strip()[:4000]
            prompt = (
                "次のPDFをデータカタログで説明する日本語の概要を、20字以上30字以内の"
                "一文で作成してください。推測や前置き、引用符は不要です。\n"
                f"タイトル: {title}\nファイル名: {filename}\n本文抜粋: {excerpt}"
            )
            messages = [
                {
                    "role": "system",
                    "content": "あなたはEnterprise文書カタログの編集者です。",
                },
                {"role": "user", "content": prompt},
            ]
            # Reasoning-capable endpoints can consume a small token budget
            # without emitting visible content. Retry one empty/malformed
            # response with a larger budget; this remains in the asynchronous
            # parsing task and never delays or fails the upload response.
            last_candidate = ""
            for attempt, max_tokens in enumerate((512, 900), start=1):
                attempt_messages = messages
                if attempt == 2:
                    attempt_messages = [
                        *messages,
                        {
                            "role": "user",
                            "content": "概要本文だけを20字以上30字以内で必ず出力してください。",
                        },
                    ]
                raw = self.gateway.invoke_model(
                    model_target,
                    attempt_messages,
                    max_tokens=max_tokens,
                )
                candidate = _model_text(raw)
                if candidate.strip():
                    last_candidate = candidate
                summary = _normalize_generated_summary(candidate)
                if summary is not None:
                    return summary, "AI_GENERATED", model_key
            normalized = _normalize_llm_candidate(last_candidate)
            if normalized is not None:
                return normalized, "AI_GENERATED_NORMALIZED", model_key
            return fallback, "FILENAME_FALLBACK", model_key
        except Exception:
            # Upload has already succeeded. Never turn an optional catalog
            # enrichment failure into a parsing failure.
            return fallback, "FILENAME_FALLBACK", model_key

    def create_preparation_run(
        self,
        project_id: str,
        principal: str,
        request: PreparationRequest,
        *,
        activate_on_success: bool = True,
    ) -> dict[str, Any]:
        self._validate_documents(project_id, request.document_ids)
        profile = self.resolve_index_profile(
            request.index_profile_key,
            request.configuration,
        )
        embedding_target = self.resolve_model_target(
            request.configuration.embedding_model_key,
            "embedding",
        )
        if embedding_target != profile.embedding_endpoint:
            raise AppError(
                "INDEX_PROFILE_EMBEDDING_MISMATCH",
                "既存IndexのEmbedding endpointと選択したモデルが一致しません。管理者に設定確認を依頼してください。",
                status_code=422,
            )
        if not self.settings.prep_job_id:
            raise ResourceNotReadyError(
                "チャンク作成Jobが未設定です。AppへPREP_JOB_IDを追加してください。",
                missing=["PREP_JOB_ID"],
            )
        normalized = request.model_dump(mode="json")
        # This flag is server-controlled: the public request schema does not
        # expose it.  It is included in the persisted hash that the Lakeflow
        # Job verifies before changing the Project's active Variant pointer.
        # Omission is the signed legacy/default value True.  Persist the field
        # only for the deletion workflow's non-active successors so ordinary
        # build retries keep their historical idempotency hash.
        if not activate_on_success:
            normalized["activate_on_success"] = False
        # Document order does not change the resulting corpus. Canonicalizing it
        # makes retries from another browser tab resolve to the same hash.
        normalized["document_ids"] = sorted(normalized["document_ids"])
        canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        config_hash = hashlib.sha256(canonical.encode()).hexdigest()
        table = self.table("toyota_rag_prep_runs")

        existing = self._find_active_preparation_runs(
            project_id,
            config_hash=config_hash,
            oldest_first=True,
        )
        if existing:
            existing[0]["reused"] = True
            return self._ensure_preparation_job(project_id, existing[0])

        prep_run_id = str(uuid.uuid4())
        variant_id = str(uuid.uuid4())
        self.gateway.execute_sql(f"""
            INSERT INTO {table} (
              prep_run_id, project_id, run_type, document_ids, target_variant_id,
              requested_by, status, normalized_config_json, config_hash,
              current_step, completed_steps, total_steps, created_at
            ) VALUES (
              {sql_string(prep_run_id)}, {sql_string(project_id)}, 'BUILD_VARIANT',
              {sql_string_array(normalized['document_ids'])}, {sql_string(variant_id)},
              {sql_string(principal)}, 'QUEUED', {sql_string(canonical)},
              {sql_string(config_hash)}, 'chunking', 0, 4, current_timestamp()
            )
        """)

        # Re-read after INSERT before launching the Job. This closes the normal
        # two-request race: every contender sees the oldest active row, and only
        # its owner is allowed to call run_now.
        contenders = self._find_active_preparation_runs(
            project_id,
            config_hash=config_hash,
            oldest_first=True,
        )
        winner = contenders[0] if contenders else None
        if winner and winner.get("prep_run_id") != prep_run_id:
            self.gateway.execute_sql(f"""
                UPDATE {table} SET status='CANCELED', current_step='deduplicated',
                  completed_at=current_timestamp(), error_message=NULL
                WHERE project_id={sql_string(project_id)}
                  AND prep_run_id={sql_string(prep_run_id)}
                  AND status IN ({PREP_ACTIVE_STATUS_SQL})
            """)
            winner["reused"] = True
            return self._ensure_preparation_job(project_id, winner)

        own_run = winner or self._preparation_response(
            project_id,
            {
                "prep_run_id": prep_run_id,
                "project_id": project_id,
                "run_type": "BUILD_VARIANT",
                "target_variant_id": variant_id,
                "status": "QUEUED",
                "current_step": "chunking",
                "completed_steps": 0,
                "total_steps": 4,
                "error_message": None,
                "config_hash": config_hash,
                "job_run_id": None,
                "created_at": None,
                "started_at": None,
                "completed_at": None,
            },
        )
        own_run["reused"] = False
        return self._ensure_preparation_job(project_id, own_run)

    def _ensure_preparation_job(
        self,
        project_id: str,
        run: dict[str, Any],
    ) -> dict[str, Any]:
        """Recover an unsubmitted, server-validated BUILD_VARIANT control row.

        ``run_now`` can succeed server-side and still fail at the client before
        its run ID is persisted. Therefore an exception must leave the durable
        row active. A later read retries with the same idempotency token and
        receives the same Databricks Job run instead of creating a duplicate.
        """

        prep_run_id = require_uuid(str(run["prep_run_id"]), "prep_run_id")
        if str(run.get("project_id") or "") != project_id:
            raise ValueError("Preparation run does not belong to the requested Project")
        status = str(run.get("status") or "").upper()
        job_run_id = _int_or_none(run.get("job_run_id"))
        if (
            str(run.get("run_type") or "") != "BUILD_VARIANT"
            or status not in PREP_ACTIVE_STATUSES
            or job_run_id is not None
        ):
            self._clear_preparation_submission_state(prep_run_id)
            return run

        now = self._monotonic()
        with self._prep_submission_lock:
            if prep_run_id in self._prep_submission_inflight:
                return self._preparation_submission_wait_response(
                    project_id,
                    run,
                    queue_reason="JOB_SUBMITTING",
                    retry_after_ms=PREP_SUBMISSION_INFLIGHT_RETRY_MS,
                )
            cached_job_run_id = self._prep_submission_claims.get(prep_run_id)
            retry = self._prep_submission_retries.get(prep_run_id)
            if cached_job_run_id is None and retry is not None and now < retry[1]:
                retry_after_ms = max(1, int((retry[1] - now) * 1000))
                return self._preparation_submission_wait_response(
                    project_id,
                    run,
                    queue_reason="SUBMISSION_RETRY",
                    retry_after_ms=retry_after_ms,
                )
            self._prep_submission_inflight.add(prep_run_id)

        table = self.table("toyota_rag_prep_runs")
        try:
            job_run_id = cached_job_run_id
            if job_run_id is None:
                try:
                    job_run_id = self.gateway.run_job(
                        self.settings.prep_job_id,
                        "prep_run_id",
                        prep_run_id,
                    )
                except Exception:
                    # The request may have reached Databricks even when the
                    # client observed an exception. Never mark the row FAILED;
                    # retrying the same token is the only ambiguity-safe action.
                    failed_at = self._monotonic()
                    with self._prep_submission_lock:
                        previous_attempts = self._prep_submission_retries.get(
                            prep_run_id, (0, failed_at)
                        )[0]
                        attempts = previous_attempts + 1
                        delay_seconds = min(
                            PREP_SUBMISSION_RETRY_BASE_SECONDS
                            * (2 ** min(attempts - 1, 4)),
                            PREP_SUBMISSION_RETRY_MAX_SECONDS,
                        )
                        self._prep_submission_retries[prep_run_id] = (
                            attempts,
                            failed_at + delay_seconds,
                        )
                    return self._preparation_submission_wait_response(
                        project_id,
                        run,
                        queue_reason="SUBMISSION_RETRY",
                        retry_after_ms=int(delay_seconds * 1000),
                    )

                with self._prep_submission_lock:
                    self._prep_submission_claims[prep_run_id] = job_run_id
                    self._prep_submission_retries.pop(prep_run_id, None)

            # run_job uses prep_run_id as its Databricks idempotency token. Even
            # after an App restart, another request recovers this same run ID.
            try:
                self.gateway.execute_sql(f"""
                    UPDATE {table} SET job_run_id={job_run_id}
                    WHERE project_id={sql_string(project_id)}
                      AND prep_run_id={sql_string(prep_run_id)}
                      AND job_run_id IS NULL
                      AND status IN ({PREP_ACTIVE_STATUS_SQL})
                """)
            except Exception:
                # Keep the process-local claim so the next GET retries only the
                # SQL persistence step. A new App process safely calls run_now
                # again with the same idempotency token to recover the run ID.
                return self._preparation_response(
                    project_id,
                    {**run, "job_run_id": job_run_id},
                    reused=bool(run.get("reused")),
                )

            with self._prep_submission_lock:
                self._prep_submission_claims.pop(prep_run_id, None)
            return self._preparation_response(
                project_id,
                {**run, "job_run_id": job_run_id},
                reused=bool(run.get("reused")),
            )
        finally:
            with self._prep_submission_lock:
                self._prep_submission_inflight.discard(prep_run_id)

    def _clear_preparation_submission_state(self, prep_run_id: str) -> None:
        with self._prep_submission_lock:
            self._prep_submission_retries.pop(prep_run_id, None)
            self._prep_submission_claims.pop(prep_run_id, None)

    def _preparation_submission_wait_response(
        self,
        project_id: str,
        run: dict[str, Any],
        *,
        queue_reason: str,
        retry_after_ms: int,
    ) -> dict[str, Any]:
        response = self._preparation_response(
            project_id,
            run,
            reused=bool(run.get("reused")),
        )
        message = (
            "Lakeflow Jobへの登録処理が進行中です。"
            if queue_reason == "JOB_SUBMITTING"
            else (
                "Lakeflow Jobの受付結果を確認できなかったため、自動的に再確認します。"
                "長時間続く場合はJob権限と設定を確認してください。"
            )
        )
        response.update(
            job_state="QUEUED",
            job_state_message=message,
            queue_reason=queue_reason,
            retry_after_ms=max(1, retry_after_ms),
        )
        return response

    def get_preparation_run(self, project_id: str, run_id: str) -> dict[str, Any]:
        run_id = require_uuid(run_id, "run_id")
        table = self.table("toyota_rag_prep_runs")
        rows = self.gateway.query(f"""
            SELECT {PREP_RUN_COLUMNS}
            FROM {table}
            WHERE project_id={sql_string(project_id)} AND prep_run_id={sql_string(run_id)}
            LIMIT 1
        """)
        if not rows:
            raise NotFoundError("データ準備runが見つかりません。")
        response = self._preparation_response(project_id, rows[0])
        return self._ensure_preparation_job(project_id, response)

    def get_active_preparation_run(self, project_id: str) -> dict[str, Any] | None:
        """Return the newest still-active preparation run for page recovery."""

        active = self._find_active_preparation_runs(project_id, oldest_first=False)
        if not active:
            return None
        # This GET-side repair cannot submit browser-provided configuration. It
        # only resumes a persisted BUILD_VARIANT row previously created by the
        # Editor-only POST. The Job validates its signed config hash and Project
        # scope again before creating any physical resource.
        return self._ensure_preparation_job(project_id, active[0])

    def _find_active_preparation_runs(
        self,
        project_id: str,
        *,
        config_hash: str | None = None,
        oldest_first: bool,
    ) -> list[dict[str, Any]]:
        table = self.table("toyota_rag_prep_runs")
        config_filter = ""
        if config_hash is not None:
            config_filter = f"AND config_hash={sql_string(config_hash)}"
        direction = "ASC" if oldest_first else "DESC"
        rows = self.gateway.query(f"""
            SELECT {PREP_RUN_COLUMNS}
            FROM {table}
            WHERE project_id={sql_string(project_id)}
              AND run_type='BUILD_VARIANT'
              AND status IN ({PREP_ACTIVE_STATUS_SQL})
              {config_filter}
            ORDER BY created_at {direction}, prep_run_id {direction}
            LIMIT 100
        """)
        # A Job can terminate before its notebook updates Delta (for example, a
        # cold-start cancellation). Reconcile every candidate before deciding
        # that it is reusable or resumable.
        active: list[dict[str, Any]] = []
        for row in rows:
            response = self._preparation_response(project_id, row)
            if str(response.get("status") or "").upper() in PREP_ACTIVE_STATUSES:
                active.append(response)
                break
        return active

    def _preparation_response(
        self,
        project_id: str,
        raw_row: dict[str, Any],
        *,
        reused: bool | None = None,
    ) -> dict[str, Any]:
        row = dict(raw_row)
        status = str(row.get("status") or "UNKNOWN").upper()
        run_type = str(row.get("run_type") or "")
        job_run_id = _int_or_none(row.get("job_run_id"))
        response: dict[str, Any] = {
            **row,
            "status": status,
            "completed_steps": _int_or_none(row.get("completed_steps")) or 0,
            "total_steps": _int_or_none(row.get("total_steps")) or 1,
            "job_run_id": job_run_id,
            "job_run_url": None,
            "job_state": None,
            "job_state_message": None,
            "queue_reason": None,
            "retry_after_ms": None,
            "status_url": (
                f"/api/projects/{project_id}/preparation-runs/{row['prep_run_id']}"
            ),
        }
        if reused is not None:
            response["reused"] = reused

        if job_run_id is None:
            if run_type == "BUILD_VARIANT" and status in PREP_ACTIVE_STATUSES:
                response.update(
                    job_state="QUEUED",
                    job_state_message="Lakeflow Jobへの登録を準備しています。",
                    queue_reason="JOB_SUBMITTING",
                )
            return response

        try:
            job = self.gateway.get_job_run(job_run_id)
        except (ResourceNotReadyError, AttributeError):
            # The Delta control row remains usable when the Jobs API is briefly
            # unavailable; the browser can safely retry this read.
            response.update(
                job_state="UNKNOWN",
                job_state_message="Lakeflow Jobの状態を一時的に取得できません。",
            )
            return response

        response.update(
            job_run_url=job.get("job_run_url"),
            job_state=job.get("job_state"),
            job_state_message=job.get("job_state_message"),
            queue_reason=job.get("queue_reason"),
        )
        reconciled = _prep_status_from_job(
            str(job.get("job_state") or ""),
            str(job.get("result_state") or ""),
        )
        if status in PREP_ACTIVE_STATUSES and reconciled is not None:
            next_status, next_step, public_error = reconciled
            error_sql = sql_string(public_error) if public_error else "NULL"
            try:
                self.gateway.execute_sql(f"""
                    UPDATE {self.table('toyota_rag_prep_runs')}
                    SET status={sql_string(next_status)}, current_step={sql_string(next_step)},
                      completed_at=current_timestamp(), error_message={error_sql}
                    WHERE project_id={sql_string(project_id)}
                      AND prep_run_id={sql_string(str(row['prep_run_id']))}
                      AND status IN ({PREP_ACTIVE_STATUS_SQL})
                """)
            except ResourceNotReadyError:
                # Reflect the authoritative terminal Job state in this response;
                # a later poll can retry persistence without hiding the result.
                pass
            response.update(status=next_status, current_step=next_step, error_message=public_error)
            if next_status == "SUCCEEDED":
                response["completed_steps"] = response["total_steps"]
        return response

    def list_variants(self, project_id: str) -> list[dict[str, Any]]:
        table = self.table("toyota_index_variants")
        rows = self.gateway.query(f"""
            SELECT variant_id, chunk_method, chunk_size, cleaning_enabled,
                   semantic_metadata_enabled, embedding_model_key, config_hash,
                   source_table, index_name,
                   CAST(created_at AS STRING) AS created_at
            FROM {table}
            WHERE project_id={sql_string(project_id)} AND index_name IS NOT NULL
              AND coalesce(lifecycle_status, 'READY')='READY'
            ORDER BY created_at DESC LIMIT 200
        """)
        # A project can still contain historical Variants created before the
        # App was switched to administrator-provisioned existing Indexes.  Do
        # not let one such legacy row make the whole selector unavailable.
        # Listing is restricted to the same exact resource-pair allow-list
        # that resolve_search_target enforces when a search actually runs.
        profiles_by_pair = {
            (profile.source_table, profile.index_name): profile
            for profile in self.settings.resolved_index_profiles
        }
        items = []
        for row in rows:
            profile = profiles_by_pair.get(
                (
                    str(row.get("source_table") or ""),
                    str(row.get("index_name") or ""),
                )
            )
            if profile is None:
                continue
            items.append({
                **row,
                "index_profile_key": profile.profile_key,
                "chunk_size": _int_or_none(row.get("chunk_size")),
                "cleaning_enabled": _bool(row.get("cleaning_enabled")),
                "semantic_metadata_enabled": _bool(row.get("semantic_metadata_enabled")),
                "status": "READY",
            })
        return items

    def resolve_index(self, project_id: str, variant_id: str) -> str:
        index_name, _ = self.resolve_search_target(project_id, variant_id)
        return index_name

    def resolve_search_target(self, project_id: str, variant_id: str) -> tuple[str, str]:
        """Return an allow-listed physical Index and the actual logical Variant ID."""

        table = self.table("toyota_index_variants")
        if variant_id == "default":
            rows = self.gateway.query(f"""
                SELECT v.variant_id, v.source_table, v.index_name
                FROM {self.table('toyota_rag_projects')} p
                INNER JOIN {table} v
                  ON p.project_id=v.project_id
                 AND p.active_variant_id=v.variant_id
                WHERE p.project_id={sql_string(project_id)}
                  AND v.index_name IS NOT NULL
                  AND coalesce(v.lifecycle_status, 'READY')='READY'
                LIMIT 1
            """)
            if not rows:
                raise NotFoundError("このProjectには既定のIndex Variantがありません。")
        else:
            variant_id = require_variant_id(variant_id)
            rows = self.gateway.query(f"""
                SELECT variant_id, source_table, index_name FROM {table}
                WHERE project_id={sql_string(project_id)}
                  AND variant_id={sql_string(variant_id)} AND index_name IS NOT NULL
                  AND coalesce(lifecycle_status, 'READY')='READY'
                LIMIT 1
            """)
            if not rows:
                raise NotFoundError("選択したIndex Variantは利用できません。")
        row = rows[0]
        self._profile_for_resource_pair(
            str(row.get("source_table") or ""),
            str(row.get("index_name") or ""),
        )
        resolved_variant_id = require_variant_id(
            str(row.get("variant_id") or ""),
        )
        return str(row["index_name"]), resolved_variant_id

    def _validate_documents(self, project_id: str, document_ids: list[str]) -> None:
        validated = [require_uuid(item, "document_id") for item in document_ids]
        table = self.table("toyota_document_registry")
        rows = self.gateway.query(f"""
            SELECT document_id FROM {table}
            WHERE project_id={sql_string(project_id)}
              AND document_id IN ({', '.join(sql_string(item) for item in validated)})
              AND processing_status IN ('PARSED', 'READY')
              AND coalesce(lifecycle_status, 'ACTIVE')='ACTIVE'
        """)
        found = {row["document_id"] for row in rows}
        if found != set(validated):
            raise NotFoundError("未解析または別ProjectのPDFが含まれています。")

    def get_document_content(self, project_id: str, document_id: str) -> tuple[bytes, str]:
        document_id = require_uuid(document_id, "document_id")
        cache_key = (project_id, document_id)
        now = self._monotonic()
        with self._document_cache_lock:
            cached = self._document_cache.get(cache_key)
            if cached is not None:
                expires_at, data, filename = cached
                if expires_at > now:
                    self._document_cache.move_to_end(cache_key)
                    return data, filename
                self._document_cache.pop(cache_key, None)
                self._document_cache_bytes -= len(data)
        table = self.table("toyota_document_registry")
        rows = self.gateway.query(f"""
            SELECT doc_uri, original_filename FROM {table}
            WHERE project_id={sql_string(project_id)}
              AND document_id={sql_string(document_id)}
              LIMIT 1
        """)
        if not rows:
            raise NotFoundError("PDFが見つかりません。")
        data = self.gateway.download_volume_file(str(rows[0]["doc_uri"]))
        filename = str(rows[0].get("original_filename") or "document.pdf")
        self._cache_document_content(project_id, document_id, data, filename)
        return data, filename

    def _cache_document_content(
        self,
        project_id: str,
        document_id: str,
        data: bytes,
        filename: str,
    ) -> None:
        if len(data) > DOCUMENT_CACHE_MAX_BYTES:
            return
        key = (project_id, document_id)
        with self._document_cache_lock:
            old = self._document_cache.pop(key, None)
            if old is not None:
                self._document_cache_bytes -= len(old[1])
            self._document_cache[key] = (
                self._monotonic() + DOCUMENT_CACHE_TTL_SECONDS,
                data,
                filename,
            )
            self._document_cache_bytes += len(data)
            while (
                len(self._document_cache) > DOCUMENT_CACHE_MAX_ITEMS
                or self._document_cache_bytes > DOCUMENT_CACHE_MAX_BYTES
            ):
                _, (_, evicted, _) = self._document_cache.popitem(last=False)
                self._document_cache_bytes -= len(evicted)

    def _evict_project_documents(self, project_id: str) -> None:
        with self._document_cache_lock:
            for key in [key for key in self._document_cache if key[0] == project_id]:
                _, data, _ = self._document_cache.pop(key)
                self._document_cache_bytes -= len(data)

    def _evict_document(self, project_id: str, document_id: str) -> None:
        key = (project_id, document_id)
        with self._document_cache_lock:
            cached = self._document_cache.pop(key, None)
            if cached is not None:
                self._document_cache_bytes -= len(cached[1])

    def list_sessions(self, project_id: str, principal: str) -> list[dict[str, Any]]:
        table = self.table("toyota_rag_chat_sessions")
        return self.gateway.query(f"""
            SELECT session_id, title, status, CAST(updated_at AS STRING) AS updated_at
            FROM {table}
            WHERE project_id={sql_string(project_id)}
              AND owner_principal={sql_string(principal)} AND status='ACTIVE'
            ORDER BY updated_at DESC LIMIT 100
        """)

    def create_session(self, project_id: str, principal: str, title: str) -> dict[str, Any]:
        session_id = str(uuid.uuid4())
        table = self.table("toyota_rag_chat_sessions")
        self.gateway.execute_sql(f"""
            INSERT INTO {table} (session_id, project_id, title, owner_principal,
              status, created_at, updated_at)
            VALUES ({sql_string(session_id)}, {sql_string(project_id)}, {sql_string(title)},
              {sql_string(principal)}, 'ACTIVE', current_timestamp(), current_timestamp())
        """)
        return {"session_id": session_id, "title": title, "status": "ACTIVE", "messages": []}

    def delete_session(
        self,
        project_id: str,
        session_id: str,
        principal: str,
    ) -> dict[str, Any]:
        """Delete one user's conversation after fencing concurrent chat runs."""

        session_id = require_uuid(session_id, "session_id")
        sessions = self.table("toyota_rag_chat_sessions")
        messages = self.table("toyota_rag_chat_messages")
        runs = self.table("toyota_rag_chat_runs")
        owner = self.gateway.query(f"""
            SELECT session_id, status FROM {sessions}
            WHERE project_id={sql_string(project_id)}
              AND session_id={sql_string(session_id)}
              AND owner_principal={sql_string(principal)}
              AND status IN ('ACTIVE', 'DELETING')
            LIMIT 1
        """)
        if not owner:
            raise NotFoundError("会話が見つかりません。")

        active = self._active_session_runs(project_id, session_id, principal)
        if active:
            self._request_session_run_cancellation(project_id, session_id, principal)
            raise AppError(
                "SESSION_RUN_ACTIVE",
                "回答を停止しています。完了後にもう一度会話を削除してください。",
                status_code=409,
                retryable=True,
                details={"request_ids": active},
            )

        # Fencing ACTIVE -> DELETING prevents begin_chat_run's source query
        # from accepting another turn while records are removed.
        self._execute_chat_write(f"""
            UPDATE {sessions} SET status='DELETING', updated_at=current_timestamp()
            WHERE project_id={sql_string(project_id)}
              AND session_id={sql_string(session_id)}
              AND owner_principal={sql_string(principal)}
              AND status='ACTIVE'
        """)
        raced = self._active_session_runs(project_id, session_id, principal)
        if raced:
            self._request_session_run_cancellation(project_id, session_id, principal)
            self._execute_chat_write(f"""
                UPDATE {sessions} SET status='ACTIVE', updated_at=current_timestamp()
                WHERE project_id={sql_string(project_id)}
                  AND session_id={sql_string(session_id)}
                  AND owner_principal={sql_string(principal)}
                  AND status='DELETING'
            """)
            raise AppError(
                "SESSION_RUN_ACTIVE",
                "回答を停止しています。完了後にもう一度会話を削除してください。",
                status_code=409,
                retryable=True,
                details={"request_ids": raced},
            )

        # Child-first deletion is retry-safe: the session fence remains until
        # both dependent tables have been removed.
        self._execute_chat_write(f"""
            DELETE FROM {messages}
            WHERE project_id={sql_string(project_id)}
              AND session_id={sql_string(session_id)}
        """)
        self._execute_chat_write(f"""
            DELETE FROM {runs}
            WHERE project_id={sql_string(project_id)}
              AND session_id={sql_string(session_id)}
        """)
        self._execute_chat_write(f"""
            DELETE FROM {sessions}
            WHERE project_id={sql_string(project_id)}
              AND session_id={sql_string(session_id)}
              AND owner_principal={sql_string(principal)}
              AND status='DELETING'
        """)
        return {"session_id": session_id, "deleted": True}

    def _active_session_runs(
        self, project_id: str, session_id: str, principal: str
    ) -> list[str]:
        rows = self.gateway.query(f"""
            SELECT r.request_id FROM {self.table('toyota_rag_chat_runs')} r
            INNER JOIN {self.table('toyota_rag_chat_sessions')} s
              ON r.project_id=s.project_id AND r.session_id=s.session_id
            WHERE r.project_id={sql_string(project_id)}
              AND r.session_id={sql_string(session_id)}
              AND s.owner_principal={sql_string(principal)}
              AND (
                r.status IN ('QUEUED', 'STREAMING', 'CANCEL_REQUESTED')
                OR EXISTS (
                  SELECT 1 FROM {self.table('toyota_rag_chat_messages')} m
                  WHERE m.project_id=r.project_id
                    AND m.session_id=r.session_id
                    AND m.request_id=r.request_id
                    AND m.role='assistant'
                    AND m.generation_status='STREAMING'
                )
              )
            LIMIT 100
        """)
        request_ids: list[str] = []
        for row in rows:
            try:
                request_ids.append(require_uuid(str(row.get("request_id") or ""), "request_id"))
            except NotFoundError:
                continue
        return request_ids

    def _request_session_run_cancellation(
        self, project_id: str, session_id: str, principal: str
    ) -> None:
        self._execute_chat_write(f"""
            UPDATE {self.table('toyota_rag_chat_runs')} SET
              status='CANCEL_REQUESTED', cancel_requested_at=current_timestamp()
            WHERE project_id={sql_string(project_id)}
              AND session_id={sql_string(session_id)}
              AND status IN ('QUEUED', 'STREAMING')
              AND EXISTS (
                SELECT 1 FROM {self.table('toyota_rag_chat_sessions')} s
                WHERE s.project_id={sql_string(project_id)}
                  AND s.session_id={sql_string(session_id)}
                  AND s.owner_principal={sql_string(principal)}
              )
        """)

    def require_session(self, project_id: str, session_id: str, principal: str) -> None:
        session_id = require_uuid(session_id, "session_id")
        table = self.table("toyota_rag_chat_sessions")
        rows = self.gateway.query(f"""
            SELECT session_id FROM {table}
            WHERE project_id={sql_string(project_id)}
              AND session_id={sql_string(session_id)}
              AND owner_principal={sql_string(principal)} AND status='ACTIVE' LIMIT 1
        """)
        if not rows:
            raise NotFoundError("会話が見つかりません。")

    def get_session(self, project_id: str, session_id: str, principal: str) -> dict[str, Any]:
        session_id = require_uuid(session_id, "session_id")
        sessions = self.table("toyota_rag_chat_sessions")
        messages = self.table("toyota_rag_chat_messages")
        # A history click used to execute require_session, header and message
        # lookups serially.  The authorised LEFT JOIN below returns the same
        # payload in one Warehouse statement, including an empty conversation.
        rows = self.gateway.query(f"""
            SELECT s.session_id, s.title, s.status,
                   CAST(s.updated_at AS STRING) AS updated_at,
                   m.message_id, m.sequence_no, m.role, m.content,
                   m.generation_status, m.request_id, m.trace_id,
                   to_json(m.citations) AS citations,
                   CAST(m.created_at AS STRING) AS created_at
            FROM {sessions} s
            LEFT JOIN {messages} m
              ON s.project_id=m.project_id AND s.session_id=m.session_id
            WHERE s.project_id={sql_string(project_id)}
              AND s.session_id={sql_string(session_id)}
              AND s.owner_principal={sql_string(principal)}
              AND s.status='ACTIVE'
            ORDER BY m.sequence_no DESC LIMIT 500
        """)
        if not rows:
            raise NotFoundError("会話が見つかりません。")
        first = rows[0]
        header = {
            "session_id": first["session_id"],
            "title": first["title"],
            "status": first["status"],
            "updated_at": first.get("updated_at"),
        }
        public_messages = []
        for row in reversed(rows):
            if not row.get("message_id"):
                continue
            row["sequence_no"] = _int_or_none(row.get("sequence_no"))
            row["citations"] = _public_citations(
                json_or_value(row.get("citations"), []), project_id
            )
            row["trace_href"] = self.trace_href(row.get("trace_id"))
            for key in ("session_id", "title", "status", "updated_at"):
                row.pop(key, None)
            public_messages.append(row)
        return {**header, "messages": public_messages}

    def trace_href(self, trace_id: str | None) -> str | None:
        """Build a trusted Workspace UI link for a persisted MLflow Trace ID."""
        if not trace_id or not SAFE_TRACE_ID.fullmatch(trace_id):
            return None
        host = self.settings.databricks_host
        experiment_id = self.settings.mlflow_experiment_id
        if not host or not experiment_id:
            return None
        return (
            f"{host.rstrip('/')}/ml/experiments/{experiment_id}/traces"
            f"?selectedTraceId={trace_id}"
        )

    def begin_chat_run(
        self,
        *,
        project_id: str,
        session_id: str,
        principal: str,
        user_message: str,
        config: dict[str, Any],
        request_id: str | None = None,
    ) -> dict[str, Any]:
        project_id = require_uuid(project_id, "project_id")
        session_id = require_uuid(session_id, "session_id")
        request_id = (
            require_uuid(request_id, "client_request_id")
            if request_id
            else str(uuid.uuid4())
        )
        request_hash = _chat_request_hash(user_message, config)
        snapshot_value = {**config, "_request_hash": request_hash}
        snapshot = json.dumps(
            snapshot_value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        user_message_id = str(
            uuid.uuid5(
                CHAT_MESSAGE_NAMESPACE,
                f"{project_id}:{session_id}:{request_id}:user",
            )
        )
        assistant_message_id = str(
            uuid.uuid5(
                CHAT_MESSAGE_NAMESPACE,
                f"{project_id}:{session_id}:{request_id}:assistant",
            )
        )
        # Preserve chronological ordering while avoiding SELECT MAX races.
        # UUID entropy supplies 500,000 slots within the request's millisecond.
        sequence_base = int(time.time_ns() // 1_000_000) * 1_000_000
        sequence_slot = (uuid.UUID(request_id).int % 500_000) * 2
        user_sequence = sequence_base + sequence_slot
        assistant_sequence = user_sequence + 1
        creation_claim = f"CHAT_CREATE:{uuid.uuid4()}"
        messages = self.table("toyota_rag_chat_messages")
        runs = self.table("toyota_rag_chat_runs")
        sessions = self.table("toyota_rag_chat_sessions")
        with self._chat_creation_lock:
            existing = self._get_chat_request(
                project_id=project_id,
                session_id=session_id,
                request_id=request_id,
                principal=principal,
            )
            if (
                existing is not None
                and existing.get("user_message_id")
                and existing.get("message_id")
            ):
                return self._reuse_chat_request(
                    existing,
                    request_hash=request_hash,
                    user_message=user_message,
                    config=config,
                )

            # MERGE makes client_request_id the durable creation key. If an
            # HTTP retry or another App replica wins first, this statement is a
            # no-op and the message MERGE below converges on the same IDs.
            self._execute_chat_write(f"""
                MERGE INTO {runs} AS target
                USING (
                  SELECT {sql_string(request_id)} AS request_id,
                         {sql_string(project_id)} AS project_id,
                         {sql_string(session_id)} AS session_id,
                         {sql_string(creation_claim)} AS creation_claim
                  FROM {sessions}
                  WHERE project_id={sql_string(project_id)}
                    AND session_id={sql_string(session_id)}
                    AND owner_principal={sql_string(principal)}
                    AND status='ACTIVE'
                ) AS source
                ON target.project_id=source.project_id
                  AND target.request_id=source.request_id
                WHEN NOT MATCHED THEN INSERT
                  (request_id, project_id, session_id, status, started_at,
                   error_message)
                VALUES
                  (source.request_id, source.project_id, source.session_id,
                   'STREAMING', current_timestamp(), source.creation_claim)
            """)
            self._execute_chat_write(f"""
                MERGE INTO {messages} AS target
                USING (
                  SELECT {sql_string(user_message_id)} AS message_id,
                         r.project_id, r.session_id,
                         {user_sequence} AS sequence_no,
                         'user' AS role,
                         {sql_string(user_message)} AS content,
                         r.request_id,
                         'COMPLETED' AS generation_status,
                         {sql_string(snapshot)} AS config_snapshot_json
                  FROM {runs} r
                  INNER JOIN {sessions} s
                    ON r.project_id=s.project_id AND r.session_id=s.session_id
                  WHERE r.project_id={sql_string(project_id)}
                    AND r.session_id={sql_string(session_id)}
                    AND r.request_id={sql_string(request_id)}
                    AND s.owner_principal={sql_string(principal)}
                    AND s.status='ACTIVE'
                  UNION ALL
                  SELECT {sql_string(assistant_message_id)} AS message_id,
                         r.project_id, r.session_id,
                         {assistant_sequence} AS sequence_no,
                         'assistant' AS role,
                         '' AS content,
                         r.request_id,
                         'STREAMING' AS generation_status,
                         {sql_string(snapshot)} AS config_snapshot_json
                  FROM {runs} r
                  INNER JOIN {sessions} s
                    ON r.project_id=s.project_id AND r.session_id=s.session_id
                  WHERE r.project_id={sql_string(project_id)}
                    AND r.session_id={sql_string(session_id)}
                    AND r.request_id={sql_string(request_id)}
                    AND s.owner_principal={sql_string(principal)}
                    AND s.status='ACTIVE'
                ) AS source
                ON target.project_id=source.project_id
                  AND target.session_id=source.session_id
                  AND target.request_id=source.request_id
                  AND target.role=source.role
                WHEN NOT MATCHED THEN INSERT
                  (message_id, project_id, session_id, sequence_no, role,
                   content, request_id, generation_status,
                   config_snapshot_json, created_at)
                VALUES
                  (source.message_id, source.project_id, source.session_id,
                   source.sequence_no, source.role, source.content,
                   source.request_id, source.generation_status,
                   source.config_snapshot_json, current_timestamp())
            """)
            created = self._get_chat_request(
                project_id=project_id,
                session_id=session_id,
                request_id=request_id,
                principal=principal,
            )
            if created is None:
                # A request ID already bound to another session must never be
                # silently reused, even though that session is not disclosed.
                raise AppError(
                    "IDEMPOTENCY_CONFLICT",
                    "同じリクエストIDが別の会話で使用されています。新しい質問として再送してください。",
                    status_code=409,
                )
            result = self._reuse_chat_request(
                created,
                request_hash=request_hash,
                user_message=user_message,
                config=config,
            )
            is_creator = created.get("creation_claim") == creation_claim
            result["reused"] = not is_creator
            if is_creator:
                self._execute_chat_write(f"""
                    UPDATE {runs} SET error_message=NULL
                    WHERE project_id={sql_string(project_id)}
                      AND request_id={sql_string(request_id)}
                      AND status='STREAMING'
                      AND error_message={sql_string(creation_claim)}
                """)
            self.gateway.execute_sql(f"""
                UPDATE {sessions} SET updated_at=current_timestamp()
                WHERE project_id={sql_string(project_id)}
                  AND session_id={sql_string(session_id)}
                  AND owner_principal={sql_string(principal)}
            """)
            return result

    def _get_chat_request(
        self,
        *,
        project_id: str,
        session_id: str,
        request_id: str,
        principal: str,
    ) -> dict[str, Any] | None:
        runs = self.table("toyota_rag_chat_runs")
        sessions = self.table("toyota_rag_chat_sessions")
        messages = self.table("toyota_rag_chat_messages")
        rows = self.gateway.query(f"""
            SELECT r.request_id, r.session_id, r.status,
                   r.error_message AS creation_claim,
                   u.message_id AS user_message_id,
                   u.content AS user_message,
                   u.config_snapshot_json,
                   a.message_id,
                   a.content AS answer,
                   to_json(a.citations) AS citations,
                   a.trace_id
            FROM {runs} r
            INNER JOIN {sessions} s
              ON r.project_id=s.project_id AND r.session_id=s.session_id
            LEFT JOIN {messages} u
              ON r.project_id=u.project_id AND r.session_id=u.session_id
             AND r.request_id=u.request_id AND u.role='user'
            LEFT JOIN {messages} a
              ON r.project_id=a.project_id AND r.session_id=a.session_id
             AND r.request_id=a.request_id AND a.role='assistant'
            WHERE r.project_id={sql_string(project_id)}
              AND r.session_id={sql_string(session_id)}
              AND r.request_id={sql_string(request_id)}
              AND s.owner_principal={sql_string(principal)}
              AND s.status='ACTIVE'
            ORDER BY u.created_at ASC, a.created_at ASC
            LIMIT 1
        """)
        return dict(rows[0]) if rows else None

    def _reuse_chat_request(
        self,
        row: dict[str, Any],
        *,
        request_hash: str,
        user_message: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        stored_snapshot = json_or_value(row.get("config_snapshot_json"), {})
        if not isinstance(stored_snapshot, dict):
            stored_snapshot = {}
        stored_hash = stored_snapshot.pop("_request_hash", None)
        if not stored_hash:
            # Rows written by an earlier app version remain safely reusable.
            stored_hash = _chat_request_hash(
                str(row.get("user_message") or ""), stored_snapshot
            )
        if (
            stored_hash != request_hash
            or str(row.get("user_message") or "") != user_message
            or stored_snapshot != config
        ):
            raise AppError(
                "IDEMPOTENCY_CONFLICT",
                "同じリクエストIDに異なる質問または設定は指定できません。",
                status_code=409,
            )
        if not row.get("user_message_id") or not row.get("message_id"):
            raise ResourceNotReadyError(
                "質問の受付処理が完了していません。少し待ってから同じリクエストIDで再試行してください。"
            )
        citations = json_or_value(row.get("citations"), [])
        return {
            "request_id": str(row["request_id"]),
            "message_id": str(row["message_id"]),
            "user_message_id": str(row["user_message_id"]),
            "session_id": str(row["session_id"]),
            "status": str(row.get("status") or "UNKNOWN").upper(),
            "answer": str(row.get("answer") or ""),
            "citations": citations if isinstance(citations, list) else [],
            "trace_id": row.get("trace_id"),
            "reused": True,
        }

    def _execute_chat_write(self, statement: str) -> None:
        """Retry an idempotent chat MERGE/guarded UPDATE once.

        Delta optimistic concurrency can reject one of two simultaneous App
        replicas. Every caller of this helper uses a MERGE key or a guarded
        state transition, so replaying the statement cannot duplicate a turn
        or overwrite a different terminal state.
        """
        try:
            self.gateway.execute_sql(statement)
        except Exception:
            time.sleep(0.05)
            self.gateway.execute_sql(statement)

    def complete_chat_run(
        self,
        *,
        project_id: str,
        request_id: str,
        message_id: str,
        answer: str,
        citations: list[dict[str, Any]],
        status: str,
        trace_id: str | None = None,
    ) -> str:
        if status not in {"COMPLETED", "CANCELED"}:
            raise ValueError("unsupported chat terminal status")
        messages = self.table("toyota_rag_chat_messages")
        runs = self.table("toyota_rag_chat_runs")
        citation_sql = _citation_array_sql(citations)
        generation = "CANCELLED" if status == "CANCELED" else "COMPLETED"
        allowed_statuses = (
            "('QUEUED', 'STREAMING', 'CANCEL_REQUESTED')"
            if status == "CANCELED"
            else "('QUEUED', 'STREAMING')"
        )
        # The run is the durable source of truth. Claim its terminal state
        # first, then persist the assistant payload only if that state won.
        self._execute_chat_write(f"""
            UPDATE {runs} SET status={sql_string(status)},
              completed_at=current_timestamp(), error_message=NULL
            WHERE project_id={sql_string(project_id)}
              AND request_id={sql_string(request_id)}
              AND status IN {allowed_statuses}
        """)
        actual_status = self.get_chat_run_status(project_id, request_id)
        if actual_status == status:
            self._execute_chat_write(f"""
                UPDATE {messages} SET content={sql_string(answer)},
                  generation_status={sql_string(generation)}, citations={citation_sql},
                  trace_id={sql_string(trace_id) if trace_id else 'NULL'}
                WHERE project_id={sql_string(project_id)}
                  AND message_id={sql_string(message_id)}
                  AND request_id={sql_string(request_id)}
                  AND role='assistant'
                  AND EXISTS (
                    SELECT 1 FROM {runs} r
                    WHERE r.project_id={sql_string(project_id)}
                      AND r.request_id={sql_string(request_id)}
                      AND r.status={sql_string(status)}
                  )
            """)
        return actual_status

    def fail_chat_run(
        self,
        project_id: str,
        request_id: str,
        message_id: str,
        *,
        trace_id: str | None = None,
    ) -> str:
        messages = self.table("toyota_rag_chat_messages")
        runs = self.table("toyota_rag_chat_runs")
        self._execute_chat_write(f"""
            UPDATE {runs} SET status='ERROR', error_message='回答生成に失敗しました',
              completed_at=current_timestamp()
            WHERE project_id={sql_string(project_id)} AND request_id={sql_string(request_id)}
              AND status IN ('QUEUED', 'STREAMING')
        """)
        actual_status = self.get_chat_run_status(project_id, request_id)
        if actual_status == "ERROR":
            self._execute_chat_write(f"""
                UPDATE {messages} SET generation_status='ERROR',
                  trace_id={sql_string(trace_id) if trace_id else 'NULL'}
                WHERE project_id={sql_string(project_id)}
                  AND message_id={sql_string(message_id)}
                  AND request_id={sql_string(request_id)}
                  AND role='assistant'
                  AND EXISTS (
                    SELECT 1 FROM {runs} r
                    WHERE r.project_id={sql_string(project_id)}
                      AND r.request_id={sql_string(request_id)}
                      AND r.status='ERROR'
                  )
            """)
        return actual_status

    def get_chat_run_status(self, project_id: str, request_id: str) -> str:
        request_id = require_uuid(request_id, "request_id")
        table = self.table("toyota_rag_chat_runs")
        rows = self.gateway.query(f"""
            SELECT status FROM {table}
            WHERE project_id={sql_string(project_id)}
              AND request_id={sql_string(request_id)}
            LIMIT 1
        """)
        if not rows:
            raise NotFoundError("回答runが見つかりません。")
        return str(rows[0].get("status") or "UNKNOWN").upper()

    def request_chat_cancel(self, project_id: str, request_id: str, principal: str) -> str:
        request_id = require_uuid(request_id, "request_id")
        table = self.table("toyota_rag_chat_runs")
        sessions = self.table("toyota_rag_chat_sessions")
        rows = self.gateway.query(f"""
            SELECT r.request_id, r.status FROM {table} r
            INNER JOIN {sessions} s
              ON r.project_id=s.project_id AND r.session_id=s.session_id
            WHERE r.project_id={sql_string(project_id)}
              AND r.request_id={sql_string(request_id)}
              AND s.owner_principal={sql_string(principal)} LIMIT 1
        """)
        if not rows:
            raise NotFoundError("停止対象の回答runが見つかりません。")
        current_status = str(rows[0].get("status") or "UNKNOWN").upper()
        if current_status in CHAT_TERMINAL_STATUSES:
            return current_status
        self._execute_chat_write(f"""
            UPDATE {table} SET status='CANCEL_REQUESTED', cancel_requested_at=current_timestamp()
            WHERE project_id={sql_string(project_id)} AND request_id={sql_string(request_id)}
              AND status IN ('QUEUED', 'STREAMING')
        """)
        # The guarded UPDATE can lose to completion. Return what was actually
        # persisted so the API never claims a completed run is still stopping.
        rows = self.gateway.query(f"""
            SELECT r.status FROM {table} r
            INNER JOIN {sessions} s
              ON r.project_id=s.project_id AND r.session_id=s.session_id
            WHERE r.project_id={sql_string(project_id)}
              AND r.request_id={sql_string(request_id)}
              AND s.owner_principal={sql_string(principal)} LIMIT 1
        """)
        if not rows:
            raise NotFoundError("停止対象の回答runが見つかりません。")
        return str(rows[0].get("status") or "UNKNOWN").upper()

    def resolve_metadata_filters(
        self,
        question: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        vehicle_master = self.table("toyota_vehicle_master")

        # Calls without a Project ID are retained for compatibility with older
        # callers and evaluation assets that expect the Toyota-specific scalar
        # filters. Online chat always supplies a Project ID and uses the safer,
        # universally indexed document_id filter below.
        if project_id is None:
            vehicle_rows = self.gateway.query(f"""
                SELECT model, to_json(aliases) AS aliases,
                       to_json(valid_model_years) AS years, vehicle_category
                FROM {vehicle_master} WHERE active=TRUE LIMIT 500
            """)
            return _resolve_vehicle_master_filters(question, vehicle_rows)

        canonical_project_id = require_uuid(project_id, "project_id")
        registry = self.table("toyota_document_registry")
        document_rows = self.gateway.query(f"""
            SELECT document_id, model, model_year, document_type, vehicle_category,
                   category, to_json(tags) AS tags,
                   CAST(document_date AS STRING) AS document_date,
                   source, metadata_json
            FROM {registry}
            WHERE project_id={sql_string(canonical_project_id)}
              AND processing_status IN ('PARSED', 'READY')
            ORDER BY document_id
            LIMIT 500
        """)
        if not document_rows:
            return {}

        generic_ids = _resolve_generic_document_filter_set(question, document_rows)

        # A vehicle name such as "Crown" can also be an ordinary Project or
        # product name. Consult the vehicle master only when this Project's own
        # registry actually contains legacy vehicle metadata, then restrict the
        # master candidates to those registered models. This prevents a generic
        # Project from receiving an unrelated Toyota filter that returns zero
        # search results.
        registered_models = {
            str(value).strip().casefold()
            for row in document_rows
            if (value := _registry_metadata_value(row, "model", "legacy"))
            and str(value).strip()
        }
        legacy_ids: set[str] | None = None
        if registered_models:
            vehicle_rows = self.gateway.query(f"""
                SELECT model, to_json(aliases) AS aliases,
                       to_json(valid_model_years) AS years, vehicle_category
                FROM {vehicle_master} WHERE active=TRUE LIMIT 500
            """)
            project_vehicle_rows = [
                row for row in vehicle_rows
                if registered_models.intersection(_vehicle_master_names(row))
            ]
            vehicle_filters = _resolve_vehicle_master_filters(
                question,
                project_vehicle_rows,
            )
            legacy_ids = _resolve_legacy_document_filter_set(
                vehicle_filters,
                project_vehicle_rows,
                document_rows,
            )

        resolutions = [
            resolution
            for resolution in (generic_ids, legacy_ids)
            if resolution is not None
        ]
        if not resolutions:
            return {}
        document_ids = set(resolutions[0])
        for resolution in resolutions[1:]:
            document_ids.intersection_update(resolution)

        all_ids = _registry_document_ids(document_rows)
        # Conflicting metadata, whole-corpus matches, and unusually large IN
        # lists fall back to retrieval without a metadata filter. This is safer
        # than hiding all evidence or sending an oversized AI Search request.
        if not document_ids or document_ids == all_ids or len(document_ids) > 100:
            return {}
        return {"document_id": sorted(document_ids)}

    def list_evaluation_datasets(self, project_id: str) -> list[dict[str, Any]]:
        project_id = require_uuid(project_id, "project_id")
        table = self.table("toyota_rag_eval_cases")
        rows = self.gateway.query(f"""
            SELECT dataset_version, dataset_split, COUNT(*) AS case_count,
                   CAST(MAX(created_at) AS STRING) AS updated_at
            FROM {table}
            WHERE project_id={sql_string(project_id)}
            GROUP BY dataset_version, dataset_split
            ORDER BY dataset_version DESC, dataset_split
        """)
        return [
            {
                "dataset_version": str(row["dataset_version"]),
                "dataset_split": str(row["dataset_split"]),
                "case_count": _int_or_none(row.get("case_count")) or 0,
                "updated_at": row.get("updated_at"),
            }
            for row in rows
        ]

    def list_evaluation_cases(
        self,
        project_id: str,
        dataset_version: str,
        dataset_split: str,
    ) -> list[dict[str, Any]]:
        project_id = require_uuid(project_id, "project_id")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", dataset_version):
            raise AppError("VALIDATION_ERROR", "評価データ版が不正です。", status_code=422)
        if dataset_split not in {"development", "holdout"}:
            raise AppError("VALIDATION_ERROR", "評価データ用途が不正です。", status_code=422)
        cases = self.table("toyota_rag_eval_cases")
        registry = self.table("toyota_document_registry")
        rows = self.gateway.query(f"""
            SELECT c.eval_case_id, c.question, c.expected_answer,
                   to_json(c.expected_facts) AS expected_facts,
                   c.relevant_doc_uri, to_json(c.relevant_pages) AS relevant_pages,
                   c.question_type, c.is_answerable, c.language,
                   c.dataset_version, c.dataset_split,
                   CAST(c.created_at AS STRING) AS created_at,
                   r.document_id, r.title AS document_title
            FROM {cases} c
            LEFT JOIN {registry} r
              ON c.project_id=r.project_id AND c.relevant_doc_uri=r.doc_uri
            WHERE c.project_id={sql_string(project_id)}
              AND c.dataset_version={sql_string(dataset_version)}
              AND c.dataset_split={sql_string(dataset_split)}
            ORDER BY c.created_at, c.eval_case_id
            LIMIT 1000
        """)
        items: list[dict[str, Any]] = []
        for row in rows:
            items.append({
                **row,
                "expected_facts": [
                    str(item) for item in json_or_value(row.get("expected_facts"), [])
                ],
                "relevant_pages": [
                    int(item) for item in json_or_value(row.get("relevant_pages"), [])
                ],
                "is_answerable": _bool(row.get("is_answerable")),
            })
        return items

    def create_evaluation_case(
        self,
        project_id: str,
        principal: str,
        payload: EvaluationCaseCreate,
    ) -> dict[str, Any]:
        project_id = require_uuid(project_id, "project_id")
        cases = self.table("toyota_rag_eval_cases")
        registry = self.table("toyota_document_registry")
        projects = self.table("toyota_rag_projects")
        document_id: str | None = None
        document_title: str | None = None
        relevant_doc_uri: str | None = None
        if payload.relevant_document_id:
            document_id = require_uuid(payload.relevant_document_id, "relevant_document_id")
            documents = self.gateway.query(f"""
                SELECT document_id, doc_uri, title, page_count
                FROM {registry}
                WHERE project_id={sql_string(project_id)}
                  AND document_id={sql_string(document_id)}
                  AND processing_status IN ('PARSED', 'READY')
                LIMIT 2
            """)
            if len(documents) != 1:
                raise AppError(
                    "INVALID_EVALUATION_CASE",
                    "正解PDFがこのプロジェクトにないか、解析が完了していません。",
                    status_code=422,
                )
            document = documents[0]
            page_count = _int_or_none(document.get("page_count"))
            if page_count is not None and payload.relevant_pages and max(payload.relevant_pages) > page_count:
                raise AppError(
                    "INVALID_EVALUATION_CASE",
                    f"正解ページはPDFの{page_count}ページ以内で指定してください。",
                    status_code=422,
                )
            relevant_doc_uri = str(document["doc_uri"])
            document_title = str(document.get("title") or "PDF")

        duplicates = self.gateway.query(f"""
            SELECT eval_case_id FROM {cases}
            WHERE project_id={sql_string(project_id)}
              AND dataset_version={sql_string(payload.dataset_version)}
              AND dataset_split={sql_string(payload.dataset_split)}
              AND lower(trim(question))=lower(trim({sql_string(payload.question)}))
            LIMIT 1
        """)
        if duplicates:
            raise AppError(
                "DUPLICATE_EVALUATION_CASE",
                "同じ質問は、この評価データ版・用途に登録済みです。",
                status_code=409,
            )

        eval_case_id = str(uuid.uuid4())
        expected_facts = (
            sql_string_array(payload.expected_facts)
            if payload.expected_facts
            else "CAST(array() AS ARRAY<STRING>)"
        )
        relevant_pages = (
            "array(" + ", ".join(str(page) for page in payload.relevant_pages) + ")"
            if payload.relevant_pages
            else "CAST(array() AS ARRAY<INT>)"
        )
        relevance_judgments = (
            "array(" + ", ".join(
                "named_struct('doc_uri', " + sql_string(relevant_doc_uri)
                + ", 'page_number', " + str(page) + ", 'relevance_grade', 3)"
                for page in payload.relevant_pages
            ) + ")"
            if relevant_doc_uri and payload.relevant_pages
            else "CAST(array() AS ARRAY<STRUCT<doc_uri:STRING,page_number:INT,relevance_grade:INT>>)"
        )
        self.gateway.execute_sql(f"""
            INSERT INTO {cases} (
              project_id, eval_case_id, question, expected_answer, expected_facts,
              relevant_doc_uri, relevant_pages, relevance_judgments, expected_filter,
              model, model_year, question_type, is_answerable, language,
              dataset_version, dataset_split, created_at
            ) VALUES (
              {sql_string(project_id)}, {sql_string(eval_case_id)},
              {sql_string(payload.question)}, {sql_string(payload.expected_answer)},
              {expected_facts}, {sql_string(relevant_doc_uri)}, {relevant_pages},
              {relevance_judgments}, NULL, NULL, NULL,
              {sql_string(payload.question_type)}, {str(payload.is_answerable).lower()},
              {sql_string(payload.language)}, {sql_string(payload.dataset_version)},
              {sql_string(payload.dataset_split)}, current_timestamp()
            )
        """)
        self.gateway.execute_sql(f"""
            UPDATE {projects}
            SET active_dataset_version={sql_string(payload.dataset_version)},
                updated_at=current_timestamp()
            WHERE project_id={sql_string(project_id)}
              AND EXISTS (
                SELECT 1 FROM {self.table('toyota_rag_project_members')}
                WHERE project_id={sql_string(project_id)}
                  AND principal={sql_string(principal)}
              )
        """)
        return {
            "eval_case_id": eval_case_id,
            "question": payload.question,
            "expected_answer": payload.expected_answer,
            "expected_facts": payload.expected_facts,
            "document_id": document_id,
            "document_title": document_title,
            "relevant_pages": payload.relevant_pages,
            "question_type": payload.question_type,
            "is_answerable": payload.is_answerable,
            "language": payload.language,
            "dataset_version": payload.dataset_version,
            "dataset_split": payload.dataset_split,
        }

    def create_evaluation_run(
        self,
        project_id: str,
        principal: str,
        request: EvaluationRequest,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.resolve_index(project_id, request.variant_id)
        self.resolve_model_target(request.answer_model_key, "chat")
        self.resolve_model_target(request.judge_model_key, "judge")
        selected_case_ids = self._validate_evaluation_case_selection(
            project_id,
            dataset_version=request.dataset_version,
            dataset_split=request.dataset_split,
            evaluation_case_ids=request.evaluation_case_ids,
        )
        if not self.settings.eval_job_id:
            raise ResourceNotReadyError(
                "精度評価Jobが未設定です。AppへEVAL_JOB_IDを追加してください。",
                missing=["EVAL_JOB_ID"],
            )
        request_data = request.model_dump(mode="json")
        canonical = json.dumps(request_data, ensure_ascii=False,
                               sort_keys=True, separators=(",", ":"))
        config_hash = hashlib.sha256(canonical.encode()).hexdigest()
        eval_run_id = _evaluation_run_id(project_id, principal, idempotency_key)
        table = self.table("toyota_rag_eval_runs")
        previous = self.gateway.query(f"""
            SELECT eval_run_id, phase_id, status, config_hash,
                   expected_trials, completed_trials, error_message, job_run_id,
                   CAST(created_at AS STRING) AS created_at,
                   CAST(completed_at AS STRING) AS completed_at,
                   CAST(GREATEST(
                     0,
                     timestampdiff(
                       SECOND, created_at,
                       COALESCE(completed_at, current_timestamp())
                     )
                   ) AS BIGINT) AS elapsed_seconds
            FROM {table}
            WHERE project_id={sql_string(project_id)}
              AND requested_by={sql_string(principal)}
              AND get_json_object(config_json, '$._idempotency_key')={sql_string(idempotency_key)}
            ORDER BY created_at, eval_run_id, phase_id
        """)
        if previous:
            if any(row.get("config_hash") != config_hash for row in previous):
                raise AppError(
                    "IDEMPOTENCY_CONFLICT",
                    "同じIdempotency-Keyが異なる評価設定で使用されています。",
                    status_code=409,
                )
            existing_id = str(previous[0]["eval_run_id"])
            existing_rows = [
                row for row in previous if str(row.get("eval_run_id")) == existing_id
            ]
            response = self._evaluation_response(
                project_id, existing_id, existing_rows
            )
            response = self._ensure_evaluation_job(project_id, response)
            response.update(
                config_hash=config_hash,
                selected_case_count=len(selected_case_ids),
                status_url=(
                    f"/api/projects/{project_id}/evaluation-runs/{existing_id}"
                ),
            )
            return response
        stored_config = json.dumps(
            {**request_data, "_idempotency_key": idempotency_key},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        expected_trials = len(selected_case_ids) * int(request.trial_count)
        source_rows = []
        for phase in request.phase_ids:
            source_rows.append("SELECT " + ", ".join([
                f"{sql_string(project_id)} AS project_id",
                f"{sql_string(eval_run_id)} AS eval_run_id",
                f"{sql_string(phase)} AS phase_id",
                f"{sql_string(request.variant_id)} AS variant_id",
                f"{sql_string(request.dataset_version)} AS dataset_version",
                f"{sql_string(request.dataset_split)} AS dataset_split",
                f"{sql_string(request.answer_model_key)} AS answer_model_key",
                f"{sql_string(request.judge_model_key)} AS judge_model_key",
                f"{int(request.trial_count)} AS trial_count",
                f"{sql_string(principal)} AS requested_by",
                "'QUEUED' AS status",
                f"{expected_trials} AS expected_trials",
                "0 AS completed_trials",
                f"{sql_string(stored_config)} AS config_json",
                f"{sql_string(config_hash)} AS config_hash",
            ]))
        merge_statement = f"""
            MERGE INTO {table} AS target
            USING ({' UNION ALL '.join(source_rows)}) AS source
            ON target.project_id=source.project_id
              AND target.eval_run_id=source.eval_run_id
              AND target.phase_id=source.phase_id
            WHEN NOT MATCHED THEN INSERT (
              project_id, eval_run_id, phase_id, variant_id, dataset_version,
              dataset_split, answer_model_key, judge_model_key, trial_count,
              requested_by, status, expected_trials, completed_trials,
              config_json, config_hash, created_at
            ) VALUES (
              source.project_id, source.eval_run_id, source.phase_id,
              source.variant_id, source.dataset_version, source.dataset_split,
              source.answer_model_key, source.judge_model_key,
              source.trial_count, source.requested_by, source.status,
              source.expected_trials, source.completed_trials,
              source.config_json, source.config_hash, current_timestamp()
            )
        """
        try:
            self.gateway.execute_sql(merge_statement)
        except Exception:
            # A concurrent App replica can commit the same deterministic rows
            # first. Replaying the key-only MERGE cannot duplicate a Phase.
            time.sleep(0.05)
            self.gateway.execute_sql(merge_statement)

        created = self.gateway.query(f"""
            SELECT eval_run_id, phase_id, status, config_hash,
                   expected_trials, completed_trials, error_message, job_run_id,
                   CAST(created_at AS STRING) AS created_at,
                   CAST(completed_at AS STRING) AS completed_at,
                   CAST(GREATEST(
                     0,
                     timestampdiff(
                       SECOND, created_at,
                       COALESCE(completed_at, current_timestamp())
                     )
                   ) AS BIGINT) AS elapsed_seconds
            FROM {table}
            WHERE project_id={sql_string(project_id)}
              AND eval_run_id={sql_string(eval_run_id)}
            ORDER BY phase_id
        """)
        expected_phases = set(request.phase_ids)
        if not created:
            raise ResourceNotReadyError(
                "精度評価の受付状態を確認できません。少し待ってから再試行してください。"
            )
        if (
            any(str(row.get("config_hash") or "") != config_hash for row in created)
            or {str(row.get("phase_id") or "") for row in created} != expected_phases
        ):
            raise AppError(
                "IDEMPOTENCY_CONFLICT",
                "同じIdempotency-Keyが異なる評価設定で使用されています。",
                status_code=409,
            )
        response = self._evaluation_response(project_id, eval_run_id, created)
        response = self._ensure_evaluation_job(project_id, response)
        response.update(
            config_hash=config_hash,
            selected_case_count=len(selected_case_ids),
            status_url=f"/api/projects/{project_id}/evaluation-runs/{eval_run_id}",
        )
        return response

    def _validate_evaluation_case_selection(
        self,
        project_id: str,
        *,
        dataset_version: str,
        dataset_split: str,
        evaluation_case_ids: list[str] | None,
    ) -> list[str]:
        """Return the exact Project-scoped case IDs accepted for a new run."""

        project_id = require_uuid(project_id, "project_id")
        cases = self.table("toyota_rag_eval_cases")
        selection_predicate = ""
        if evaluation_case_ids is not None:
            selection_predicate = (
                "AND eval_case_id IN ("
                + ", ".join(sql_string(item) for item in evaluation_case_ids)
                + ")"
            )
        rows = self.gateway.query(f"""
            SELECT eval_case_id
            FROM {cases}
            WHERE project_id={sql_string(project_id)}
              AND dataset_version={sql_string(dataset_version)}
              AND dataset_split={sql_string(dataset_split)}
              {selection_predicate}
            ORDER BY eval_case_id
            LIMIT 1001
        """)
        found = [str(row.get("eval_case_id") or "") for row in rows]
        if not found:
            raise AppError(
                "EVALUATION_CASES_NOT_FOUND",
                "選択した評価データに実行できる質問がありません。",
                status_code=422,
            )
        if len(found) > 1000:
            raise AppError(
                "TOO_MANY_EVALUATION_CASES",
                "一度に評価できる質問は1000件までです。",
                status_code=422,
            )
        if evaluation_case_ids is not None and set(found) != set(evaluation_case_ids):
            raise AppError(
                "INVALID_EVALUATION_CASE_SELECTION",
                "選択した質問の一部が、このプロジェクト・評価データ版・用途に属していません。",
                status_code=422,
            )
        return found

    def get_evaluation_run(self, project_id: str, eval_run_id: str) -> dict[str, Any]:
        eval_run_id = require_uuid(eval_run_id, "eval_run_id")
        table = self.table("toyota_rag_eval_runs")
        rows = self.gateway.query(f"""
            SELECT phase_id, status, expected_trials, completed_trials, error_message,
                   job_run_id,
                   CAST(created_at AS STRING) AS created_at,
                   CAST(completed_at AS STRING) AS completed_at,
                   CAST(GREATEST(
                     0,
                     timestampdiff(
                       SECOND, created_at,
                       COALESCE(completed_at, current_timestamp())
                     )
                   ) AS BIGINT) AS elapsed_seconds
            FROM {table} WHERE project_id={sql_string(project_id)}
              AND eval_run_id={sql_string(eval_run_id)} ORDER BY phase_id
        """)
        if not rows:
            raise NotFoundError("精度評価runが見つかりません。")

        response = self._evaluation_response(project_id, eval_run_id, rows)
        return self._ensure_evaluation_job(project_id, response)

    def _evaluation_response(
        self,
        project_id: str,
        eval_run_id: str,
        source_rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build one public response and reconcile a persisted Job state.

        Job submission recovery is deliberately kept in
        ``_ensure_evaluation_job``.  This method is also used by the POST path,
        where an idempotency-key retry can find an existing active control row.
        """

        rows = [dict(row) for row in source_rows]
        table = self.table("toyota_rag_eval_runs")

        normalized_job_run_ids = [
            _int_or_none(row.get("job_run_id")) for row in rows
        ]
        has_invalid_job_run_id = any(
            row.get("job_run_id") not in (None, "")
            and (job_run_id is None or job_run_id <= 0)
            for row, job_run_id in zip(rows, normalized_job_run_ids, strict=True)
        )
        present_job_run_ids = {
            job_run_id for job_run_id in normalized_job_run_ids if job_run_id is not None
        }
        if has_invalid_job_run_id or len(present_job_run_ids) > 1:
            # Every phase is inserted and assigned to one Lakeflow Job as a
            # single logical evaluation run. Fail closed instead of querying an
            # arbitrary Job when the control rows no longer agree.
            raise ResourceNotReadyError(
                "精度評価runのJob情報に不整合があります。管理者に確認してください。"
            )
        job_run_id = next(iter(present_job_run_ids), None)
        if job_run_id is not None and any(
            candidate is None for candidate in normalized_job_run_ids
        ):
            # Concurrent insert-only Delta MERGEs can commit an identical
            # control row after another App replica has persisted the shared
            # Job run ID.  The deterministic Jobs idempotency token proves
            # that the one present positive ID belongs to this logical run, so
            # repair late rows instead of leaving the UI on a permanent 503.
            try:
                self.gateway.execute_sql(f"""
                    UPDATE {table} SET job_run_id={job_run_id}
                    WHERE project_id={sql_string(project_id)}
                      AND eval_run_id={sql_string(eval_run_id)}
                      AND job_run_id IS NULL
                """)
            except ResourceNotReadyError:
                # Continue with the known-safe ID and retry the repair on the
                # next status request if the Warehouse is temporarily busy.
                pass
        elapsed_values: list[int] = []
        for row in rows:
            row["expected_trials"] = _int_or_none(row.get("expected_trials")) or 0
            row["completed_trials"] = _int_or_none(row.get("completed_trials")) or 0
            row["status"] = str(row.get("status") or "UNKNOWN").upper()
            elapsed_seconds = _int_or_none(row.get("elapsed_seconds"))
            if elapsed_seconds is not None and elapsed_seconds >= 0:
                row["elapsed_seconds"] = elapsed_seconds
                elapsed_values.append(elapsed_seconds)
            else:
                row["elapsed_seconds"] = None
            row.pop("job_run_id", None)
        statuses = {str(row["status"]) for row in rows}
        overall = _overall_evaluation_status(statuses)
        created_values = [
            str(row["created_at"]) for row in rows if row.get("created_at")
        ]
        completed_values = [
            str(row["completed_at"])
            for row in rows
            if row.get("completed_at")
        ]
        response: dict[str, Any] = {
            "eval_run_id": eval_run_id,
            "status": overall,
            "phases": _collapse_evaluation_phase_rows(rows),
            # The browser restores its elapsed timer from the durable run
            # timestamp after a reload or history selection. Concurrent,
            # identical control rows can differ by a few milliseconds, so use
            # the oldest creation and latest completion across all phases.
            "created_at": min(created_values) if created_values else None,
            "completed_at": max(completed_values) if completed_values else None,
            # TIMESTAMP strings lose their Workspace offset when serialized by
            # CAST.  Keep duration arithmetic in Databricks and expose one
            # timezone-independent value for the browser timer.  The longest
            # non-negative Phase duration covers small concurrent insert-time
            # differences without trusting malformed values.
            "elapsed_seconds": max(elapsed_values) if elapsed_values else None,
            "job_run_id": job_run_id,
            "job_run_url": None,
            "job_state": None,
            "job_state_message": None,
            "queue_reason": None,
        }
        if job_run_id is None:
            if overall in EVALUATION_ACTIVE_STATUSES:
                response.update(
                    job_state="QUEUED",
                    job_state_message="Lakeflow Jobへの登録を準備しています。",
                    queue_reason="JOB_SUBMITTING",
                )
            return response

        try:
            job = self.gateway.get_job_run(job_run_id)
        except (ResourceNotReadyError, AttributeError):
            if "CANCEL_REQUESTED" in statuses:
                self._request_evaluation_job_cancel(job_run_id)
            response.update(
                job_state="UNKNOWN",
                job_state_message="Lakeflow Jobの状態を一時的に取得できません。",
            )
            return response

        response.update(
            job_run_url=job.get("job_run_url"),
            job_state=job.get("job_state"),
            job_state_message=_evaluation_job_state_message(job),
            queue_reason=job.get("queue_reason"),
        )
        reconciled = _evaluation_status_from_job(
            str(job.get("job_state") or ""),
            str(job.get("result_state") or ""),
        )
        if reconciled is None and "CANCEL_REQUESTED" in statuses:
            self._request_evaluation_job_cancel(job_run_id)
            response.update(
                job_state_message="Lakeflow Jobへ停止を要求しています。",
                queue_reason="CANCEL_REQUESTED",
            )
        if reconciled is None or not statuses.intersection(EVALUATION_ACTIVE_STATUSES):
            return response

        next_status, public_error = reconciled
        error_sql = sql_string(public_error) if public_error else "NULL"
        try:
            self.gateway.execute_sql(f"""
                UPDATE {table}
                SET status={sql_string(next_status)}, completed_at=current_timestamp(),
                    error_message={error_sql}
                WHERE project_id={sql_string(project_id)}
                  AND eval_run_id={sql_string(eval_run_id)}
                  AND status IN ({EVALUATION_ACTIVE_STATUS_SQL})
            """)
        except ResourceNotReadyError:
            # The Jobs API is authoritative for a run that terminates before
            # the notebook starts. Reflect it now and let a later poll retry
            # the Project-scoped persistence update.
            pass
        for row in rows:
            if row["status"] in EVALUATION_ACTIVE_STATUSES:
                row["status"] = next_status
                row["error_message"] = public_error
        response["phases"] = _collapse_evaluation_phase_rows(rows)
        response["status"] = _overall_evaluation_status(
            {str(row["status"]) for row in rows}
        )
        return response

    def _ensure_evaluation_job(
        self,
        project_id: str,
        run: dict[str, Any],
    ) -> dict[str, Any]:
        """Recover an evaluation Job whose run ID was not durably saved.

        ``run_now`` can reach Databricks while the App observes a timeout, and
        the following Delta UPDATE can fail independently.  The durable
        ``eval_run_id`` is always reused as the Jobs idempotency token, so a
        later POST/GET recovers the original run instead of launching a second
        evaluation.  A cancellation request follows the same recovery path in
        order to find and stop an ambiguously submitted Job.
        """

        eval_run_id = require_uuid(str(run["eval_run_id"]), "eval_run_id")
        job_run_id = _int_or_none(run.get("job_run_id"))
        phase_statuses = {
            str(phase.get("status") or "").upper()
            for phase in run.get("phases", [])
            if isinstance(phase, dict)
        }
        has_active_phase = bool(phase_statuses.intersection(EVALUATION_ACTIVE_STATUSES))
        cancel_requested = "CANCEL_REQUESTED" in phase_statuses
        if job_run_id is not None:
            self._clear_evaluation_submission_state(eval_run_id)
            return run
        if not has_active_phase:
            self._clear_evaluation_submission_state(eval_run_id)
            return run

        now = self._monotonic()
        with self._evaluation_submission_lock:
            if eval_run_id in self._evaluation_submission_inflight:
                return self._evaluation_submission_wait_response(
                    run,
                    queue_reason="JOB_SUBMITTING",
                    retry_after_ms=EVALUATION_SUBMISSION_INFLIGHT_RETRY_MS,
                    cancel_requested=cancel_requested,
                )
            cached_job_run_id = self._evaluation_submission_claims.get(eval_run_id)
            retry = self._evaluation_submission_retries.get(eval_run_id)
            if (
                cached_job_run_id is None
                and retry is not None
                and now < retry[1]
            ):
                retry_after_ms = max(1, int((retry[1] - now) * 1000))
                return self._evaluation_submission_wait_response(
                    run,
                    queue_reason="SUBMISSION_RETRY",
                    retry_after_ms=retry_after_ms,
                    cancel_requested=cancel_requested,
                )
            self._evaluation_submission_inflight.add(eval_run_id)

        table = self.table("toyota_rag_eval_runs")
        try:
            job_run_id = cached_job_run_id
            if job_run_id is None:
                try:
                    job_run_id = self.gateway.run_job(
                        self.settings.eval_job_id,
                        "eval_run_id",
                        eval_run_id,
                    )
                except JobSubmissionRejectedError:
                    # A 4xx Jobs response is definitive.  Persist a terminal
                    # state and return the same safe App error so the progress
                    # UI stops immediately instead of retrying forever.
                    self.gateway.execute_sql(f"""
                        UPDATE {table}
                        SET status='FAILED',
                            error_message={sql_string(EVALUATION_SUBMISSION_REJECTED_MESSAGE)},
                            completed_at=current_timestamp()
                        WHERE project_id={sql_string(project_id)}
                          AND eval_run_id={sql_string(eval_run_id)}
                          AND status IN ({EVALUATION_ACTIVE_STATUS_SQL})
                    """)
                    self._clear_evaluation_submission_state(eval_run_id)
                    raise
                except Exception:
                    # Never mark the Delta rows FAILED: an exception cannot
                    # distinguish rejection from a successful server-side
                    # submission whose response was lost.
                    failed_at = self._monotonic()
                    with self._evaluation_submission_lock:
                        previous_attempts = self._evaluation_submission_retries.get(
                            eval_run_id, (0, failed_at)
                        )[0]
                        attempts = previous_attempts + 1
                        delay_seconds = min(
                            EVALUATION_SUBMISSION_RETRY_BASE_SECONDS
                            * (2 ** min(attempts - 1, 4)),
                            EVALUATION_SUBMISSION_RETRY_MAX_SECONDS,
                        )
                        self._evaluation_submission_retries[eval_run_id] = (
                            attempts,
                            failed_at + delay_seconds,
                        )
                    return self._evaluation_submission_wait_response(
                        run,
                        queue_reason="SUBMISSION_RETRY",
                        retry_after_ms=int(delay_seconds * 1000),
                        cancel_requested=cancel_requested,
                    )

                normalized_job_run_id = _int_or_none(job_run_id)
                if normalized_job_run_id is None or normalized_job_run_id <= 0:
                    raise ResourceNotReadyError(
                        "精度評価Jobの受付結果が不正です。自動的に再確認します。"
                    )
                job_run_id = normalized_job_run_id
                with self._evaluation_submission_lock:
                    self._evaluation_submission_claims[eval_run_id] = job_run_id
                    self._evaluation_submission_retries.pop(eval_run_id, None)

            try:
                self.gateway.execute_sql(f"""
                    UPDATE {table} SET job_run_id={job_run_id}
                    WHERE project_id={sql_string(project_id)}
                      AND eval_run_id={sql_string(eval_run_id)}
                      AND job_run_id IS NULL
                """)
            except Exception:
                # The process-local claim makes the next poll retry only this
                # persistence step.  After an App restart, run_now with the
                # same idempotency token safely recovers the same Job run.
                cancel_requested = self._evaluation_cancel_was_requested(
                    project_id,
                    eval_run_id,
                    fallback=cancel_requested,
                )
                # Keep observing the known Job even while its ID cannot yet be
                # saved. Otherwise a completed Job would remain displayed as
                # QUEUED forever during a persistent Warehouse write issue.
                observed = self._evaluation_response(
                    project_id,
                    eval_run_id,
                    [
                        {**phase, "job_run_id": job_run_id}
                        for phase in run.get("phases", [])
                        if isinstance(phase, dict)
                    ],
                )
                recovered = {**run, **observed}
                if not recovered.get("job_state_message"):
                    recovered["job_state_message"] = (
                        "停止対象のLakeflow Jobを確認しています。"
                        if cancel_requested
                        else "Lakeflow Jobの受付結果を保存しています。"
                    )
                if cancel_requested:
                    self._request_evaluation_job_cancel(job_run_id)
                return recovered

            with self._evaluation_submission_lock:
                self._evaluation_submission_claims.pop(eval_run_id, None)

            # Cancellation can win while run_now or the Delta UPDATE is in
            # progress. Re-read after the Job ID is durable so a successful
            # cancel response never depends on a later browser poll.
            cancel_requested = self._evaluation_cancel_was_requested(
                project_id,
                eval_run_id,
                fallback=cancel_requested,
            )
            recovered = {
                **run,
                "job_run_id": job_run_id,
                "job_state": "QUEUED",
                "job_state_message": (
                    "Lakeflow Jobへ停止を要求しています。"
                    if cancel_requested
                    else "Lakeflow Jobの開始を待っています。"
                ),
                "queue_reason": (
                    "CANCEL_REQUESTED" if cancel_requested else run.get("queue_reason")
                ),
            }
            if cancel_requested:
                self._request_evaluation_job_cancel(job_run_id)
            return recovered
        finally:
            with self._evaluation_submission_lock:
                self._evaluation_submission_inflight.discard(eval_run_id)

    def _clear_evaluation_submission_state(self, eval_run_id: str) -> None:
        with self._evaluation_submission_lock:
            self._evaluation_submission_retries.pop(eval_run_id, None)
            self._evaluation_submission_claims.pop(eval_run_id, None)

    def _evaluation_cancel_was_requested(
        self,
        project_id: str,
        eval_run_id: str,
        *,
        fallback: bool,
    ) -> bool:
        try:
            latest_rows = self.gateway.query(f"""
                SELECT status
                FROM {self.table('toyota_rag_eval_runs')}
                WHERE project_id={sql_string(project_id)}
                  AND eval_run_id={sql_string(eval_run_id)}
            """)
        except ResourceNotReadyError:
            return fallback
        return fallback or any(
            str(row.get("status") or "").upper() == "CANCEL_REQUESTED"
            for row in latest_rows
        )

    def _evaluation_submission_wait_response(
        self,
        run: dict[str, Any],
        *,
        queue_reason: str,
        retry_after_ms: int,
        cancel_requested: bool,
    ) -> dict[str, Any]:
        response = dict(run)
        if cancel_requested:
            message = "停止対象のLakeflow Jobを確認しています。"
        elif queue_reason == "JOB_SUBMITTING":
            message = "Lakeflow Jobへの登録処理が進行中です。"
        else:
            message = (
                "Lakeflow Jobの受付結果を確認できなかったため、自動的に再確認します。"
                "長時間続く場合はJob権限と設定を確認してください。"
            )
        response.update(
            job_state="QUEUED",
            job_state_message=message,
            queue_reason=queue_reason,
            retry_after_ms=max(1, retry_after_ms),
        )
        return response

    def _request_evaluation_job_cancel(self, job_run_id: int) -> None:
        try:
            self.gateway.cancel_job_run(job_run_id)
        except (ResourceNotReadyError, AttributeError):
            # The durable cancel_requested_at flag remains authoritative and
            # every later status poll retries the Jobs cancellation request.
            pass

    def list_evaluation_runs(self, project_id: str) -> list[dict[str, Any]]:
        """Return recent Project-scoped runs so completed comparisons are reusable."""
        table = self.table("toyota_rag_eval_runs")
        rows = self.gateway.query(f"""
            SELECT eval_run_id, phase_id, status, dataset_version, dataset_split,
                   variant_id, answer_model_key, judge_model_key, trial_count,
                   requested_by, job_run_id,
                   CAST(created_at AS STRING) AS created_at,
                   CAST(completed_at AS STRING) AS completed_at
            FROM {table}
            WHERE project_id={sql_string(project_id)}
            ORDER BY created_at DESC, eval_run_id, phase_id
            LIMIT 250
        """)
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            run_id = str(row["eval_run_id"])
            run = grouped.setdefault(run_id, {
                "eval_run_id": run_id,
                "dataset_version": row.get("dataset_version"),
                "dataset_split": row.get("dataset_split"),
                "variant_id": row.get("variant_id"),
                "answer_model_key": row.get("answer_model_key"),
                "judge_model_key": row.get("judge_model_key"),
                "trial_count": _int_or_none(row.get("trial_count")) or 0,
                "requested_by": row.get("requested_by"),
                "job_run_id": _int_or_none(row.get("job_run_id")),
                "created_at": row.get("created_at"),
                "completed_at": row.get("completed_at"),
                "phases": [],
            })
            phase_id = str(row.get("phase_id") or "")
            phase_status = str(row.get("status") or "UNKNOWN").upper()
            existing_phase = next(
                (
                    phase for phase in run["phases"]
                    if str(phase.get("phase_id") or "") == phase_id
                ),
                None,
            )
            if existing_phase is None:
                run["phases"].append({
                    "phase_id": phase_id,
                    "status": phase_status,
                })
            else:
                existing_phase["status"] = _overall_evaluation_status({
                    str(existing_phase.get("status") or "UNKNOWN"),
                    phase_status,
                })
            candidate_job_run_id = _int_or_none(row.get("job_run_id"))
            if run.get("job_run_id") is None and candidate_job_run_id is not None:
                run["job_run_id"] = candidate_job_run_id
            if row.get("completed_at") and not run.get("completed_at"):
                run["completed_at"] = row.get("completed_at")

        for run in grouped.values():
            run["phases"].sort(key=lambda phase: str(phase.get("phase_id") or ""))
            statuses = {str(item["status"]) for item in run["phases"]}
            run["status"] = _overall_evaluation_status(statuses)
        return list(grouped.values())[:50]

    def get_evaluation_results(self, project_id: str, eval_run_id: str) -> dict[str, Any]:
        self.get_evaluation_run(project_id, eval_run_id)
        results = self.table("toyota_rag_eval_results")
        suggestions = self.table("toyota_rag_eval_suggestions")
        metrics = self.gateway.query(f"""
            SELECT phase_id, COUNT(*) AS trials,
              AVG(retrieval_page_recall_at_10_pages) AS recall_at_10,
              AVG(retrieval_page_precision_at_10_pages) AS precision_at_10,
              AVG(retrieval_page_ndcg_at_10_pages) AS ndcg_at_10,
              AVG(CASE WHEN answer_correctness IS NULL THEN NULL
                       WHEN answer_correctness THEN 1.0 ELSE 0.0 END) AS answer_correctness,
              AVG(CASE WHEN groundedness IS NULL THEN NULL
                       WHEN groundedness THEN 1.0 ELSE 0.0 END) AS groundedness,
              AVG(CASE WHEN citation_correctness IS NULL THEN NULL
                       WHEN citation_correctness THEN 1.0 ELSE 0.0 END) AS citation_correctness,
              percentile_approx(server_ttft_ms, 0.5) AS ttft_p50_ms,
              percentile_approx(e2e_latency_ms, 0.5) AS latency_p50_ms,
              percentile_approx(e2e_latency_ms, 0.95) AS latency_p95_ms,
              AVG(CASE WHEN is_error THEN 1.0 ELSE 0.0 END) AS error_rate,
              SUM(total_tokens) AS total_tokens
            FROM {results}
            WHERE project_id={sql_string(project_id)} AND eval_run_id={sql_string(eval_run_id)}
            GROUP BY phase_id ORDER BY phase_id
        """)
        for row in metrics:
            for key in list(row):
                if key not in {"phase_id"}:
                    row[key] = _float_or_none(row[key])
        suggestion_rows = self.gateway.query(f"""
            SELECT suggestion_id, phase_id, suggestion_json, confidence,
                   CAST(created_at AS STRING) AS created_at
            FROM {suggestions}
            WHERE project_id={sql_string(project_id)} AND eval_run_id={sql_string(eval_run_id)}
            ORDER BY phase_id, created_at DESC
        """)
        public_suggestions = []
        for row in suggestion_rows:
            suggestion = json_or_value(row.get("suggestion_json"), {})
            if isinstance(suggestion, dict):
                public_suggestions.append({
                    "suggestion_id": row["suggestion_id"],
                    "phase_id": row["phase_id"],
                    "confidence": _float_or_none(row.get("confidence")),
                    "created_at": row.get("created_at"),
                    **suggestion,
                })
        return {"eval_run_id": eval_run_id, "metrics": metrics, "suggestions": public_suggestions}

    def request_evaluation_cancel(self, project_id: str, eval_run_id: str) -> str:
        eval_run_id = require_uuid(eval_run_id, "eval_run_id")
        table = self.table("toyota_rag_eval_runs")
        rows = self.gateway.query(f"""
            SELECT DISTINCT job_run_id, status
            FROM {table}
            WHERE project_id={sql_string(project_id)}
              AND eval_run_id={sql_string(eval_run_id)}
        """)
        if not rows:
            raise NotFoundError("精度評価runが見つかりません。")
        job_run_ids = {
            value for value in (_int_or_none(row.get("job_run_id")) for row in rows)
            if value is not None and value > 0
        }
        if len(job_run_ids) > 1:
            raise ResourceNotReadyError(
                "精度評価runのJob情報に不整合があります。管理者に確認してください。"
            )
        self.gateway.execute_sql(f"""
            UPDATE {table} SET status='CANCEL_REQUESTED', cancel_requested_at=current_timestamp()
            WHERE project_id={sql_string(project_id)} AND eval_run_id={sql_string(eval_run_id)}
              AND status IN ('QUEUED', 'RUNNING', 'PARTIAL')
        """)
        # Re-read after the guarded UPDATE. Completion may have won the race;
        # in that case the API must not claim that cancellation was accepted.
        # For an active row, this same read also recovers a missing Job run ID
        # and issues (or retries) the Jobs cancellation request.
        response = self.get_evaluation_run(project_id, eval_run_id)
        final_status = str(response.get("status") or "").upper()
        if final_status not in {"CANCEL_REQUESTED", "CANCELED"}:
            raise AppError(
                "EVALUATION_ALREADY_FINISHED",
                "精度評価はすでに終了しているため、停止する必要はありません。",
                status_code=409,
            )
        return final_status


def _collapse_evaluation_phase_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return one deterministic public row per Phase.

    Delta does not enforce a unique key for control tables, so concurrent
    insert-only MERGEs may materialize identical rows.  Retrieval and Job
    execution are idempotent by ``eval_run_id``; this fold prevents duplicate
    rows from inflating UI progress while retaining the most conservative
    observable status and the highest completed-trial count.
    """

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        phase_id = str(row.get("phase_id") or "")
        grouped.setdefault(phase_id, []).append(row)

    collapsed: list[dict[str, Any]] = []
    for phase_id in sorted(grouped):
        duplicates = grouped[phase_id]
        representative = dict(duplicates[0])
        representative["phase_id"] = phase_id
        representative["status"] = _overall_evaluation_status({
            str(row.get("status") or "UNKNOWN").upper() for row in duplicates
        })
        representative["expected_trials"] = max(
            (_int_or_none(row.get("expected_trials")) or 0 for row in duplicates),
            default=0,
        )
        representative["completed_trials"] = max(
            (_int_or_none(row.get("completed_trials")) or 0 for row in duplicates),
            default=0,
        )
        elapsed_values = [
            elapsed
            for row in duplicates
            if (elapsed := _int_or_none(row.get("elapsed_seconds"))) is not None
            and elapsed >= 0
        ]
        representative["elapsed_seconds"] = (
            max(elapsed_values) if elapsed_values else None
        )
        representative["error_message"] = next(
            (
                str(row["error_message"])
                for row in duplicates
                if row.get("error_message")
            ),
            None,
        )
        created_values = [
            str(row["created_at"]) for row in duplicates if row.get("created_at")
        ]
        completed_values = [
            str(row["completed_at"])
            for row in duplicates
            if row.get("completed_at")
        ]
        if created_values:
            representative["created_at"] = min(created_values)
        if completed_values:
            representative["completed_at"] = max(completed_values)
        collapsed.append(representative)
    return collapsed


def _title_from_filename(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0].strip() if "." in filename else filename.strip()
    stem = re.sub(r"\s+", " ", stem.replace("_", " ")).strip()
    return (stem or "文書")[:300]


def _fallback_document_summary(title: str) -> str:
    """Return a deterministic 20-30 character Japanese catalog summary."""

    clean_title = re.sub(r"\s+", " ", title).strip() or "この文書"
    value = f"{clean_title[:12]}の内容をまとめた検索用PDF資料です"
    if len(value) < 20:
        value += "。"
    return value[:30]


def _normalize_generated_summary(value: str) -> str | None:
    text = re.sub(r"\s+", " ", value).strip()
    text = re.sub(r"^(?:概要|要約)\s*[:：]\s*", "", text)
    text = text.strip(" \t\r\n\"'「」『』")
    # Do not silently present obviously malformed model output as a catalog
    # description. The caller will use the deterministic fallback instead.
    if not 20 <= len(text) <= 30 or any(ord(char) < 32 for char in text):
        return None
    return text


def _normalize_llm_candidate(value: str) -> str | None:
    """Fit a safe non-empty LLM candidate into the 20-30 character contract.

    This deterministic last resort preserves LLM-authored text and only adds a
    generic document suffix or an explicit ellipsis. It rejects control data,
    executable-looking markup/URLs, and structured tool output instead of
    presenting those values in the catalog.
    """

    if not isinstance(value, str) or not value or len(value) > 2000:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    folded = value.casefold()
    if (
        "```" in value
        or re.search(r"<\s*/?\s*[a-z][^>]*>", value, flags=re.IGNORECASE)
        or re.search(r"(?:https?|javascript|data|file|dbfs):", folded)
        or "/volumes/" in folded
        or re.match(r"^\s*[\[{]", value)
    ):
        return None

    text = re.sub(r"\s+", " ", value).strip()
    text = re.sub(r"^(?:[-*]\s*)?(?:概要|要約)\s*[:：]\s*", "", text)
    text = text.strip(" \t\r\n\"'「」『』")
    if not text:
        return None
    if len(text) < 20:
        stem = text.rstrip("。！？!?、 ")
        text = f"{stem}について内容をまとめた検索用PDF資料です"
    if 20 <= len(text) <= 30:
        return text

    complete = [
        index + 1
        for index, character in enumerate(text[:30])
        if 20 <= index + 1 <= 30 and character in "。！？!?"
    ]
    if complete:
        normalized = text[: complete[-1]].strip()
    else:
        boundaries = [
            index + 1
            for index, character in enumerate(text[:29])
            if 20 <= index + 1 <= 29 and character in "、；;・ "
        ]
        prefix = (
            text[: boundaries[-1]].rstrip("、；;・ ")
            if boundaries
            else text[:29].rstrip()
        )
        normalized = f"{prefix[:29]}…"
    return normalized if 20 <= len(normalized) <= 30 else None


def _model_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"]
        if isinstance(choices[0].get("text"), str):
            return choices[0]["text"]
    for key in ("output_text", "answer", "text"):
        if isinstance(payload.get(key), str):
            return payload[key]
    predictions = payload.get("predictions")
    if isinstance(predictions, list) and predictions:
        first = predictions[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            nested = _model_text(first)
            if nested:
                return nested
    # Responses-compatible endpoints return message items under ``output``.
    # Keep this parser narrow: only explicit text fields are accepted, and
    # tool arguments or reasoning content are never presented as a summary.
    output = payload.get("output")
    if isinstance(output, list):
        text_parts: list[str] = []
        for item in output:
            if not isinstance(item, dict) or item.get("type") not in {None, "message"}:
                continue
            content = item.get("content")
            if isinstance(content, str):
                text_parts.append(content)
                continue
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict) or part.get("type") not in {
                    None, "output_text", "text"
                }:
                    continue
                value = part.get("text")
                if isinstance(value, str):
                    text_parts.append(value)
        return "".join(text_parts)
    return ""


def _canonical_document_metadata(
    metadata: DocumentMetadata,
    *,
    title: str,
    category: str | None,
) -> str:
    """Build a versioned, unambiguous JSON envelope for generic metadata."""

    payload = {
        "schema_version": "1.0",
        "common": {
            "title": title,
            "summary": metadata.summary,
            "category": category,
            "tags": list(metadata.tags),
            "document_date": (
                metadata.document_date.isoformat() if metadata.document_date else None
            ),
            "source": metadata.source,
        },
        "custom": dict(metadata.custom_metadata),
        "legacy": {
            "model": metadata.model,
            "model_year": metadata.model_year,
            "document_type": metadata.document_type,
            "vehicle_category": metadata.vehicle_category,
        },
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _stored_document_metadata(value: Any) -> dict[str, Any]:
    parsed = json_or_value(value, {})
    return parsed if isinstance(parsed, dict) else {}


def resolve_generic_document_filter_ids(
    question: str,
    document_rows: list[dict[str, Any]],
) -> list[str]:
    """Resolve generic metadata mentions to Project-scoped document IDs.

    Arbitrary custom keys are not interpolated into an AI Search filter path.
    Instead, this function evaluates trusted registry values in Python and
    returns IDs for the scalar ``document_id`` IN filter supported by every
    existing Index variant.  Multiple values of one field are treated as a
    comparison (OR); constraints from different fields are combined (AND).
    """

    candidate_ids = _resolve_generic_document_filter_set(question, document_rows)
    all_ids = _registry_document_ids(document_rows)
    if (
        candidate_ids is None
        or not candidate_ids
        or candidate_ids == all_ids
        or len(candidate_ids) > 100
    ):
        return []
    return sorted(candidate_ids)


def _resolve_generic_document_filter_set(
    question: str,
    document_rows: list[dict[str, Any]],
) -> set[str] | None:
    """Return a registry-validated generic metadata constraint.

    ``None`` means that the question did not identify a usable generic field;
    an empty set represents contradictory identified fields. Keeping this
    distinction lets the Project resolver safely AND generic and legacy Toyota
    constraints without turning an unrelated vehicle-master match into a
    zero-result request.
    """

    folded_question = question.casefold()
    all_ids = _registry_document_ids(document_rows)
    if not all_ids:
        return None

    buckets: dict[tuple[str, str], set[str]] = {}
    display_keys: dict[tuple[str, str], str] = {}
    for row in document_rows:
        document_id = _registry_document_id(row)
        if not document_id:
            continue

        def add(field: str, value: Any, *, display_key: str = "") -> None:
            if value is None:
                return
            text = str(value).strip()
            if not text:
                return
            bucket_key = (field, text.casefold())
            buckets.setdefault(bucket_key, set()).add(document_id)
            display_keys[bucket_key] = display_key

        add("category", _registry_metadata_value(row, "category", "common"))
        add("source", _registry_metadata_value(row, "source", "common"))
        add(
            "document_date",
            _registry_metadata_value(row, "document_date", "common"),
        )
        tags = _registry_metadata_tags(row)
        if isinstance(tags, list):
            for tag in tags:
                add("tags", tag)
        stored = _validated_stored_document_metadata(row.get("metadata_json"))
        custom = stored.get("custom") if stored else None
        if isinstance(custom, dict):
            for key, value in custom.items():
                if _valid_custom_registry_pair(key, value):
                    add("custom:" + key.casefold(), value, display_key=key)

    matched_buckets: dict[str, list[tuple[str, set[str]]]] = {}
    for bucket_key, document_ids in buckets.items():
        field, folded_value = bucket_key
        # A filter that selects the whole corpus cannot improve precision.
        if document_ids == all_ids or not _metadata_value_is_mentioned(
            folded_value, folded_question
        ):
            continue
        if field.startswith("custom:"):
            display_key = display_keys.get(bucket_key, "").casefold()
            if not display_key or not _metadata_value_is_mentioned(
                display_key, folded_question
            ):
                continue
        matched_buckets.setdefault(field, []).append((folded_value, document_ids))

    constraints: dict[str, set[str]] = {}
    for field, candidates in matched_buckets.items():
        # Prefer the most specific Japanese value.  For example, a question
        # containing ``社内公開`` also contains ``公開`` as a substring, but it
        # must not widen the filter to every public document.
        specific = [
            (value, ids) for value, ids in candidates
            if not any(
                value != other and value in other
                for other, _ in candidates
            )
        ]
        for _, document_ids in specific:
            constraints.setdefault(field, set()).update(document_ids)

    if not constraints:
        return None
    candidate_ids = set(all_ids)
    for document_ids in constraints.values():
        candidate_ids.intersection_update(document_ids)
    return candidate_ids


def _resolve_vehicle_master_filters(
    question: str,
    vehicle_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized = question.casefold()
    year_match = re.search(r"(?<!\d)(19\d{2}|20\d{2}|2100)(?!\d)", question)
    candidate_year = int(year_match.group(1)) if year_match else None
    for row in vehicle_rows:
        names = _vehicle_master_names(row)
        if not names or not any(name in normalized for name in names):
            continue
        model = str(row.get("model") or "").strip()
        if not model:
            continue
        matched: dict[str, Any] = {"model": model}
        years = {
            year
            for item in json_or_value(row.get("years"), [])
            if (year := _int_or_none(item)) is not None
        }
        if candidate_year in years:
            matched["model_year"] = candidate_year
        if row.get("vehicle_category"):
            matched["vehicle_category"] = row["vehicle_category"]
        return matched
    return {}


def _resolve_legacy_document_filter_set(
    filters: dict[str, Any],
    vehicle_rows: list[dict[str, Any]],
    document_rows: list[dict[str, Any]],
) -> set[str] | None:
    """Resolve validated Toyota compatibility fields to document IDs."""

    model = str(filters.get("model") or "").strip()
    if not model:
        return None
    matching_master = next(
        (
            row for row in vehicle_rows
            if str(row.get("model") or "").strip().casefold() == model.casefold()
        ),
        None,
    )
    if matching_master is None:
        return None
    accepted_models = _vehicle_master_names(matching_master)
    expected_year = _int_or_none(filters.get("model_year"))
    expected_category = str(filters.get("vehicle_category") or "").strip().casefold()
    matched_ids: set[str] = set()
    for row in document_rows:
        document_id = _registry_document_id(row)
        row_model = _registry_metadata_value(row, "model", "legacy")
        if not document_id or not row_model:
            continue
        if str(row_model).strip().casefold() not in accepted_models:
            continue
        if expected_year is not None:
            row_year = _int_or_none(
                _registry_metadata_value(row, "model_year", "legacy")
            )
            if row_year != expected_year:
                continue
        if expected_category:
            row_category = _registry_metadata_value(
                row,
                "vehicle_category",
                "legacy",
            )
            if not row_category or str(row_category).strip().casefold() != expected_category:
                continue
        matched_ids.add(document_id)
    # A master hit is not a valid Project filter unless the Project registry
    # confirms it. Treat an empty match as an incidental word, not as a request
    # to hide every document.
    return matched_ids or None


def _vehicle_master_names(row: dict[str, Any]) -> set[str]:
    aliases = json_or_value(row.get("aliases"), [])
    raw_names = [row.get("model")]
    if isinstance(aliases, list):
        raw_names.extend(aliases)
    return {
        str(name).strip().casefold()
        for name in raw_names
        if name is not None and str(name).strip()
    }


def _registry_document_id(row: dict[str, Any]) -> str | None:
    value = row.get("document_id")
    if value is None:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError):
        return None


def _registry_document_ids(document_rows: list[dict[str, Any]]) -> set[str]:
    return {
        document_id
        for row in document_rows
        if (document_id := _registry_document_id(row)) is not None
    }


def _validated_stored_document_metadata(value: Any) -> dict[str, Any]:
    stored = _stored_document_metadata(value)
    if stored.get("schema_version") != "1.0":
        return {}
    return stored


def _registry_metadata_value(
    row: dict[str, Any],
    field: str,
    section: str,
) -> Any:
    direct = row.get(field)
    if direct is not None and (not isinstance(direct, str) or direct.strip()):
        return direct
    stored = _validated_stored_document_metadata(row.get("metadata_json"))
    values = stored.get(section)
    if not isinstance(values, dict):
        return None
    value = values.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return value


def _registry_metadata_tags(row: dict[str, Any]) -> list[str]:
    direct = json_or_value(row.get("tags"), None)
    if isinstance(direct, list):
        return [item for item in direct if isinstance(item, str) and item.strip()]
    stored = _validated_stored_document_metadata(row.get("metadata_json"))
    common = stored.get("common")
    tags = common.get("tags") if isinstance(common, dict) else None
    if not isinstance(tags, list):
        return []
    return [item for item in tags if isinstance(item, str) and item.strip()]


def _valid_custom_registry_pair(key: Any, value: Any) -> bool:
    return (
        isinstance(key, str)
        and isinstance(value, str)
        and 0 < len(key.strip()) <= 80
        and 0 < len(value.strip()) <= 1000
        and not any(ord(character) < 32 or ord(character) == 127 for character in key)
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _metadata_value_is_mentioned(value: str, question: str) -> bool:
    if len(value) < 2:
        return False
    date_match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value)
    if date_match:
        year, month, day = date_match.groups()
        japanese_date = f"{year}年{int(month)}月{int(day)}日"
        return value in question or japanese_date in question
    if re.fullmatch(r"[a-z0-9_.-]+", value):
        return re.search(
            rf"(?<![a-z0-9_.-]){re.escape(value)}(?![a-z0-9_.-])",
            question,
        ) is not None
    return value in question


def require_uuid(value: str, label: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (TypeError, ValueError) as exc:
        raise NotFoundError(f"{label}が不正です。") from exc
    return str(parsed)


def require_variant_id(value: str) -> str:
    """Validate both generated UUIDs and stable, human-readable seed IDs."""
    if not isinstance(value, str) or not SAFE_VARIANT_ID.fullmatch(value):
        raise NotFoundError("variant_idが不正です。")
    return value


def _chat_request_hash(user_message: str, config: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"message": user_message, "config": config},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _evaluation_run_id(
    project_id: str,
    principal: str,
    idempotency_key: str,
) -> str:
    """Return a non-reversible, scope-bound ID for one evaluation request."""

    canonical_scope = json.dumps(
        [project_id, principal, idempotency_key],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return str(uuid.uuid5(EVALUATION_RUN_NAMESPACE, canonical_scope))


def _prep_status_from_job(
    job_state: str,
    result_state: str,
) -> tuple[str, str, str | None] | None:
    """Map terminal Jobs states to the persisted preparation vocabulary."""

    lifecycle = job_state.upper()
    result = result_state.upper()
    if lifecycle not in {"TERMINATED", "INTERNAL_ERROR", "SKIPPED"}:
        return None
    if result == "SUCCESS":
        return "SUCCEEDED", "completed", None
    if result in {"CANCELED", "UPSTREAM_CANCELED"}:
        return "CANCELED", "canceled", "Lakeflow Jobはキャンセルされました。"
    return "FAILED", "failed", "Lakeflow Jobが完了できませんでした。実行ログを確認してください。"


def _evaluation_status_from_job(
    job_state: str,
    result_state: str,
) -> tuple[str, str | None] | None:
    """Map terminal Jobs states to safe evaluation control-row values."""

    lifecycle = job_state.upper()
    result = result_state.upper()
    if lifecycle not in {"TERMINATED", "INTERNAL_ERROR", "SKIPPED"}:
        return None
    if result == "SUCCESS":
        return "SUCCEEDED", None
    if result in {"CANCELED", "UPSTREAM_CANCELED"}:
        return "CANCELED", None
    return (
        "FAILED",
        "精度評価Jobが完了できませんでした。Lakeflow Jobの実行ログを確認してください。",
    )


def _evaluation_job_state_message(job: dict[str, Any]) -> str:
    """Return evaluation-specific copy without forwarding provider messages."""

    queue_reason = str(job.get("queue_reason") or "").upper()
    lifecycle = str(job.get("job_state") or "").upper()
    result = str(job.get("result_state") or "").upper()
    if queue_reason == "WAITING_FOR_JOB_CAPACITY":
        return "先行する精度評価Jobの完了を待っています。"
    if queue_reason == "COMPUTE_STARTING":
        return "Databricksコンピュートを起動しています。初回は数分かかることがあります。"
    if queue_reason == "ENVIRONMENT_STARTING":
        return "評価環境とライブラリを準備しています。"
    if queue_reason == "TASK_STARTING":
        return "精度評価Taskの開始を待っています。"
    if queue_reason == "RETRY_WAIT":
        return "一時的な問題の後、精度評価Jobの再試行を待っています。"
    if queue_reason == "BLOCKED":
        return "精度評価Jobは前提条件の完了を待っています。"
    if lifecycle == "RUNNING":
        return "検索、回答生成、採点を順番に実行しています。"
    if lifecycle == "TERMINATING":
        return "精度評価Jobを終了しています。"
    if lifecycle == "TERMINATED" and result == "SUCCESS":
        return "精度評価Jobが完了しました。"
    if lifecycle == "TERMINATED" and result == "CANCELED":
        return "精度評価Jobを停止しました。"
    if lifecycle in {"TERMINATED", "INTERNAL_ERROR", "SKIPPED"}:
        return "精度評価Jobを完了できませんでした。"
    return "精度評価Jobの状態を確認しています。"


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"true", "1", "yes"}


def _public_citations(citations: Any, project_id: str) -> list[dict[str, Any]]:
    if not isinstance(citations, list):
        return []
    public = []
    for item in citations:
        if not isinstance(item, dict) or not item.get("document_id"):
            continue
        try:
            document_id = require_uuid(str(item["document_id"]), "document_id")
        except NotFoundError:
            continue
        pages = [
            page
            for value in (item.get("page_numbers") or [])
            if (page := _int_or_none(value)) is not None and page > 0
        ]
        page = _int_or_none(pages[0]) if pages else None
        href = f"/api/projects/{project_id}/documents/{document_id}/content"
        if page:
            href += f"#page={page}"
        public.append({
            "citation_id": item.get("citation_id"),
            "document_id": document_id,
            "page_numbers": pages,
            "href": href,
        })
    return public


def _citation_array_sql(citations: list[dict[str, Any]]) -> str:
    if not citations:
        return "CAST(array() AS ARRAY<STRUCT<citation_id:STRING,document_id:STRING,page_numbers:ARRAY<INT>>>)"
    items = []
    for citation in citations:
        pages = [int(page) for page in citation.get("page_numbers", [])]
        page_sql = "array(" + ", ".join(str(page) for page in pages) + ")" if pages else "CAST(array() AS ARRAY<INT>)"
        items.append(
            "named_struct('citation_id', " + sql_string(citation["citation_id"]) +
            ", 'document_id', " + sql_string(citation["document_id"]) +
            ", 'page_numbers', " + page_sql + ")"
        )
    return "array(" + ", ".join(items) + ")"
