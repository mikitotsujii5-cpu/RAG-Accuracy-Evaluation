"""Application configuration loaded exclusively from environment variables."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any


_UC_PART = re.compile(r"^[A-Za-z0-9_-]+$")
_SAFE_PROFILE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SAFE_ENDPOINT = re.compile(r"^[A-Za-z0-9_.-]{1,256}$")
_SAFE_MODEL_KEY = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_INDEX_PROFILE_FIELDS = frozenset({
    "key",
    "source_table",
    "index_name",
    "search_endpoint",
    "chunk_method",
    "chunk_size_tokens",
    "parent_chunk_size_tokens",
    "content_profile",
    "cleaning_enabled",
    "semantic_metadata_enabled",
    "embedding_model_key",
    "embedding_endpoint",
})
_CHUNK_METHODS = frozenset({"STANDARD", "SEMANTIC", "PARENT_CHILD"})
_CHUNK_SIZES = frozenset({256, 512, 1024})
_CONTENT_PROFILES = frozenset({"TEXT_ONLY", "LAYOUT_PRESERVING"})


class SettingsError(ValueError):
    """Raised when a configured resource name is unsafe or inconsistent."""


def _infer_uc_namespace(
    volume: str | None,
    default_index_name: str | None,
) -> tuple[str | None, str | None]:
    """Infer Catalog/Schema only from server-controlled qualified resources."""

    candidates: list[tuple[str, str]] = []
    if volume:
        volume_parts = (
            volume.strip("/").split("/")[1:]
            if volume.startswith("/Volumes/")
            else volume.split(".")
        )
        if len(volume_parts) == 3:
            candidates.append((volume_parts[0], volume_parts[1]))
    if default_index_name:
        index_parts = default_index_name.split(".")
        if len(index_parts) == 3:
            candidates.append((index_parts[0], index_parts[1]))
    return candidates[0] if candidates else (None, None)


@dataclass(frozen=True, slots=True)
class IndexProfile:
    """One administrator-provisioned Delta source Table and AI Search Index.

    The browser selects a logical profile key. Physical names are resolved only
    from this server-side allow-list and are never accepted from a request.
    """

    profile_key: str
    source_table: str
    index_name: str
    search_endpoint: str
    chunk_method: str
    chunk_size_tokens: int
    parent_chunk_size_tokens: int | None
    content_profile: str
    cleaning_enabled: bool
    semantic_metadata_enabled: bool
    embedding_model_key: str
    embedding_endpoint: str

    @property
    def configuration(self) -> dict[str, Any]:
        return {
            "chunk_method": self.chunk_method,
            "chunk_size_tokens": self.chunk_size_tokens,
            "parent_chunk_size_tokens": self.parent_chunk_size_tokens,
            "content_profile": self.content_profile,
            "cleaning_enabled": self.cleaning_enabled,
            "semantic_metadata_enabled": self.semantic_metadata_enabled,
            "embedding_model_key": self.embedding_model_key,
        }

    def public_dict(self) -> dict[str, Any]:
        method_label = {
            "STANDARD": "Standard",
            "SEMANTIC": "Semantic chunking",
            "PARENT_CHILD": "Parent-child chunking",
        }[self.chunk_method]
        return {
            "profile_key": self.profile_key,
            "display_name": f"{method_label} / {self.chunk_size_tokens}",
            **self.configuration,
            "index_name": self.index_name,
            "source_table": self.source_table,
            "search_endpoint": self.search_endpoint,
            "enabled": True,
            "status": "CONFIGURED",
            "unavailable_reason": None,
        }


@dataclass(frozen=True, slots=True)
class Settings:
    warehouse_id: str | None
    uc_catalog: str | None
    uc_schema: str | None
    uc_volume: str | None
    vector_search_endpoint: str | None
    default_index_name: str | None
    default_llm_endpoint: str | None
    prep_job_id: str | None
    eval_job_id: str | None
    databricks_host: str | None
    app_port: int
    local_principal: str | None
    mlflow_experiment_id: str | None = None
    mlflow_tracing_sql_warehouse_id: str | None = None
    workspace_ui_host: str | None = None
    index_profiles: tuple[IndexProfile, ...] = ()

    @classmethod
    def from_env(cls) -> "Settings":
        port_text = os.getenv("DATABRICKS_APP_PORT", os.getenv("PORT", "8000"))
        try:
            port = int(port_text)
        except ValueError as exc:
            raise SettingsError("DATABRICKS_APP_PORT は整数で指定してください。") from exc

        uc_volume = _clean(os.getenv("UC_VOLUME"))
        default_index_name = _clean(os.getenv("DEFAULT_INDEX_NAME"))
        uc_catalog = _clean(os.getenv("UC_CATALOG"))
        uc_schema = _clean(os.getenv("UC_SCHEMA"))
        # App resource bindings already provide fully qualified Volume and
        # Index names. Derive Catalog/Schema when they are not explicitly set,
        # so app.yaml never needs a user-specific catalog name.
        inferred_namespace = _infer_uc_namespace(uc_volume, default_index_name)
        uc_catalog = uc_catalog or inferred_namespace[0]
        uc_schema = uc_schema or inferred_namespace[1]
        vector_search_endpoint = _clean(os.getenv("VECTOR_SEARCH_ENDPOINT"))
        profiles = _parse_index_profiles(
            os.getenv("RAG_INDEX_PROFILES_JSON"),
            uc_catalog=uc_catalog,
            uc_schema=uc_schema,
            vector_search_endpoint=vector_search_endpoint,
            default_index_name=default_index_name,
        )
        settings = cls(
            warehouse_id=_clean(os.getenv("DATABRICKS_WAREHOUSE_ID")),
            uc_catalog=uc_catalog,
            uc_schema=uc_schema,
            uc_volume=uc_volume,
            vector_search_endpoint=vector_search_endpoint,
            default_index_name=default_index_name,
            default_llm_endpoint=_clean(os.getenv("DEFAULT_LLM_ENDPOINT")),
            prep_job_id=_clean(os.getenv("PREP_JOB_ID")),
            eval_job_id=_clean(os.getenv("EVAL_JOB_ID")),
            databricks_host=_clean(os.getenv("DATABRICKS_HOST")),
            app_port=port,
            local_principal=_clean(os.getenv("LOCAL_DEV_PRINCIPAL")),
            mlflow_experiment_id=_clean(os.getenv("MLFLOW_EXPERIMENT_ID")),
            mlflow_tracing_sql_warehouse_id=_clean(
                os.getenv("MLFLOW_TRACING_SQL_WAREHOUSE_ID")
            ),
            workspace_ui_host=_clean(os.getenv("DATABRICKS_WORKSPACE_UI_HOST")),
            index_profiles=profiles,
        )
        settings.validate_names()
        return settings

    @property
    def required_values(self) -> dict[str, str | None]:
        return {
            "DATABRICKS_WAREHOUSE_ID": self.warehouse_id,
            "UC_CATALOG": self.uc_catalog,
            "UC_SCHEMA": self.uc_schema,
            "UC_VOLUME": self.uc_volume,
            "VECTOR_SEARCH_ENDPOINT": self.vector_search_endpoint,
            "DEFAULT_INDEX_NAME": self.default_index_name,
            "DEFAULT_LLM_ENDPOINT": self.default_llm_endpoint,
            "MLFLOW_EXPERIMENT_ID": self.mlflow_experiment_id,
            "MLFLOW_TRACING_SQL_WAREHOUSE_ID": self.mlflow_tracing_sql_warehouse_id,
        }

    @property
    def missing_required(self) -> list[str]:
        return [name for name, value in self.required_values.items() if not value]

    @property
    def ready(self) -> bool:
        return not self.missing_required

    def require_ready(self) -> None:
        if self.missing_required:
            missing = "、".join(self.missing_required)
            raise SettingsError(f"Databricksリソース設定が不足しています: {missing}")

    def validate_names(self) -> None:
        for env_name, value in (
            ("PREP_JOB_ID", self.prep_job_id),
            ("EVAL_JOB_ID", self.eval_job_id),
        ):
            if value is None:
                continue
            try:
                normalized_job_id = int(value)
            except (TypeError, ValueError) as exc:
                raise SettingsError(
                    f"{env_name} は正の数値で指定してください。"
                ) from exc
            if normalized_job_id <= 0 or str(normalized_job_id) != value:
                raise SettingsError(f"{env_name} は正の数値で指定してください。")

        for env_name, value in (
            ("UC_CATALOG", self.uc_catalog),
            ("UC_SCHEMA", self.uc_schema),
        ):
            if value and not _UC_PART.fullmatch(value):
                raise SettingsError(f"{env_name} に使用できない文字が含まれています。")

        volume = self.uc_volume
        if volume and volume.startswith("/Volumes/"):
            pieces = volume.strip("/").split("/")
            if len(pieces) != 4 or pieces[0] != "Volumes":
                raise SettingsError("UC_VOLUME のVolumeパス形式が不正です。")
            for piece in pieces[1:]:
                if not _UC_PART.fullmatch(piece):
                    raise SettingsError("UC_VOLUME のVolumeパス形式が不正です。")
        elif volume:
            pieces = volume.split(".")
            if len(pieces) not in {1, 3} or not all(
                _UC_PART.fullmatch(piece) for piece in pieces
            ):
                raise SettingsError(
                    "UC_VOLUME は volume、catalog.schema.volume、または"
                    "/Volumes/catalog/schema/volume 形式で指定してください。"
                )

        if self.default_index_name:
            parts = self.default_index_name.split(".")
            if len(parts) != 3 or not all(_UC_PART.fullmatch(part) for part in parts):
                raise SettingsError("DEFAULT_INDEX_NAME は catalog.schema.index 形式で指定してください。")

        profiles = self.resolved_index_profiles
        if profiles and self.default_index_name not in {
            profile.index_name for profile in profiles
        }:
            raise SettingsError(
                "DEFAULT_INDEX_NAME はRAG_INDEX_PROFILES_JSONの許可リストに含めてください。"
            )

    @property
    def volume_path(self) -> str:
        if not self.uc_volume:
            raise SettingsError("UC_VOLUME が設定されていません。")
        if self.uc_volume.startswith("/Volumes/"):
            return self.uc_volume.rstrip("/")
        qualified_parts = self.uc_volume.split(".")
        if len(qualified_parts) == 3:
            return "/Volumes/" + "/".join(qualified_parts)
        if not self.uc_catalog or not self.uc_schema:
            raise SettingsError("Volumeパスの生成にUC_CATALOGとUC_SCHEMAが必要です。")
        return f"/Volumes/{self.uc_catalog}/{self.uc_schema}/{self.uc_volume}"

    def table_name(self, short_name: str) -> str:
        if not _UC_PART.fullmatch(short_name):
            raise SettingsError("内部Table名が不正です。")
        if not self.uc_catalog or not self.uc_schema:
            raise SettingsError("UC_CATALOGとUC_SCHEMAが設定されていません。")
        return ".".join(_quote_identifier(part) for part in (
            self.uc_catalog,
            self.uc_schema,
            short_name,
        ))

    @property
    def resolved_index_profiles(self) -> tuple[IndexProfile, ...]:
        """Return the strict allow-list, with one baseline fallback for workshops."""

        if self.index_profiles:
            _validate_profile_collection(
                self.index_profiles,
                uc_catalog=self.uc_catalog,
                uc_schema=self.uc_schema,
                vector_search_endpoint=self.vector_search_endpoint,
            )
            return self.index_profiles
        if not (
            self.uc_catalog
            and self.uc_schema
            and self.vector_search_endpoint
            and self.default_index_name
        ):
            return ()
        profiles = (_baseline_index_profile(
            uc_catalog=self.uc_catalog,
            uc_schema=self.uc_schema,
            vector_search_endpoint=self.vector_search_endpoint,
            default_index_name=self.default_index_name,
        ),)
        _validate_profile_collection(
            profiles,
            uc_catalog=self.uc_catalog,
            uc_schema=self.uc_schema,
            vector_search_endpoint=self.vector_search_endpoint,
        )
        return profiles


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _quote_identifier(value: str) -> str:
    if not _UC_PART.fullmatch(value):
        raise SettingsError("Unity Catalog識別子が不正です。")
    return f"`{value}`"


def _baseline_index_profile(
    *,
    uc_catalog: str,
    uc_schema: str,
    vector_search_endpoint: str,
    default_index_name: str,
) -> IndexProfile:
    return IndexProfile(
        profile_key="baseline-standard-512-v1",
        source_table=f"{uc_catalog}.{uc_schema}.toyota_chunks_standard_512_v1",
        index_name=default_index_name,
        search_endpoint=vector_search_endpoint,
        chunk_method="STANDARD",
        chunk_size_tokens=512,
        parent_chunk_size_tokens=None,
        content_profile="LAYOUT_PRESERVING",
        cleaning_enabled=True,
        semantic_metadata_enabled=True,
        embedding_model_key="emb-qwen3-0-6b",
        embedding_endpoint="databricks-qwen3-embedding-0-6b",
    )


def _parse_index_profiles(
    raw_json: str | None,
    *,
    uc_catalog: str | None,
    uc_schema: str | None,
    vector_search_endpoint: str | None,
    default_index_name: str | None,
) -> tuple[IndexProfile, ...]:
    if raw_json is None or not raw_json.strip():
        if not (
            uc_catalog
            and uc_schema
            and vector_search_endpoint
            and default_index_name
        ):
            return ()
        values: Any = [{
            "key": "baseline-standard-512-v1",
            "source_table": f"{uc_catalog}.{uc_schema}.toyota_chunks_standard_512_v1",
            "index_name": default_index_name,
            "search_endpoint": vector_search_endpoint,
            "chunk_method": "STANDARD",
            "chunk_size_tokens": 512,
            "parent_chunk_size_tokens": None,
            "content_profile": "LAYOUT_PRESERVING",
            "cleaning_enabled": True,
            "semantic_metadata_enabled": True,
            "embedding_model_key": "emb-qwen3-0-6b",
            "embedding_endpoint": "databricks-qwen3-embedding-0-6b",
        }]
    else:
        try:
            values = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise SettingsError("RAG_INDEX_PROFILES_JSON は正しいJSONで指定してください。") from exc
    if not isinstance(values, list) or not 1 <= len(values) <= 64:
        raise SettingsError("RAG_INDEX_PROFILES_JSON は1〜64件の配列で指定してください。")

    profiles: list[IndexProfile] = []
    for raw in values:
        if not isinstance(raw, dict) or set(raw) != _INDEX_PROFILE_FIELDS:
            raise SettingsError("RAG_INDEX_PROFILES_JSON の項目が契約と一致しません。")
        try:
            profile = IndexProfile(
                profile_key=raw["key"],
                source_table=raw["source_table"],
                index_name=raw["index_name"],
                search_endpoint=raw["search_endpoint"],
                chunk_method=raw["chunk_method"],
                chunk_size_tokens=raw["chunk_size_tokens"],
                parent_chunk_size_tokens=raw["parent_chunk_size_tokens"],
                content_profile=raw["content_profile"],
                cleaning_enabled=raw["cleaning_enabled"],
                semantic_metadata_enabled=raw["semantic_metadata_enabled"],
                embedding_model_key=raw["embedding_model_key"],
                embedding_endpoint=raw["embedding_endpoint"],
            )
        except (KeyError, TypeError) as exc:
            raise SettingsError("RAG_INDEX_PROFILES_JSON の値が不正です。") from exc
        profiles.append(profile)
    result = tuple(profiles)
    _validate_profile_collection(
        result,
        uc_catalog=uc_catalog,
        uc_schema=uc_schema,
        vector_search_endpoint=vector_search_endpoint,
    )
    return result


def _validate_profile_collection(
    profiles: tuple[IndexProfile, ...],
    *,
    uc_catalog: str | None,
    uc_schema: str | None,
    vector_search_endpoint: str | None,
) -> None:
    keys: set[str] = set()
    resource_pairs: set[tuple[str, str]] = set()
    configurations: set[str] = set()
    for profile in profiles:
        if not isinstance(profile.profile_key, str) or not _SAFE_PROFILE_KEY.fullmatch(
            profile.profile_key
        ):
            raise SettingsError("Index profile keyが不正です。")
        if profile.profile_key in keys:
            raise SettingsError("Index profile keyが重複しています。")
        keys.add(profile.profile_key)
        for field_name, value in (
            ("source_table", profile.source_table),
            ("index_name", profile.index_name),
        ):
            if not isinstance(value, str):
                raise SettingsError(f"Index profileの{field_name}が不正です。")
            parts = value.split(".")
            if (
                len(parts) != 3
                or not all(_UC_PART.fullmatch(part) for part in parts)
                or parts[:2] != [uc_catalog, uc_schema]
            ):
                raise SettingsError(
                    f"Index profileの{field_name}は設定済みCatalog.Schema内の3階層名にしてください。"
                )
        if (
            not isinstance(profile.search_endpoint, str)
            or not _SAFE_ENDPOINT.fullmatch(profile.search_endpoint)
            or profile.search_endpoint != vector_search_endpoint
        ):
            raise SettingsError(
                "Index profileのsearch_endpointはVECTOR_SEARCH_ENDPOINTと一致させてください。"
            )
        if not isinstance(profile.chunk_method, str) or profile.chunk_method not in _CHUNK_METHODS:
            raise SettingsError("Index profileのchunk_methodが不正です。")
        if (
            not isinstance(profile.chunk_size_tokens, int)
            or
            isinstance(profile.chunk_size_tokens, bool)
            or profile.chunk_size_tokens not in _CHUNK_SIZES
        ):
            raise SettingsError("Index profileのchunk_size_tokensが不正です。")
        parent = profile.parent_chunk_size_tokens
        if profile.chunk_method == "PARENT_CHILD":
            if (
                not isinstance(parent, int)
                or isinstance(parent, bool)
                or parent <= profile.chunk_size_tokens
                or parent > 8192
            ):
                raise SettingsError("Parent-child profileの親チャンクサイズが不正です。")
        elif parent is not None:
            raise SettingsError("親チャンクサイズはParent-child profileだけで指定できます。")
        if (
            not isinstance(profile.content_profile, str)
            or profile.content_profile not in _CONTENT_PROFILES
        ):
            raise SettingsError("Index profileのcontent_profileが不正です。")
        if not isinstance(profile.cleaning_enabled, bool) or not isinstance(
            profile.semantic_metadata_enabled, bool
        ):
            raise SettingsError("Index profileのON/OFF設定はbooleanで指定してください。")
        if not isinstance(profile.embedding_model_key, str) or not _SAFE_MODEL_KEY.fullmatch(
            profile.embedding_model_key
        ):
            raise SettingsError("Index profileのembedding_model_keyが不正です。")
        if not isinstance(profile.embedding_endpoint, str) or not _SAFE_ENDPOINT.fullmatch(
            profile.embedding_endpoint
        ):
            raise SettingsError("Index profileのembedding_endpointが不正です。")
        pair = (profile.source_table, profile.index_name)
        if pair in resource_pairs:
            raise SettingsError("同じsource Table／Indexを複数profileへ割り当てられません。")
        resource_pairs.add(pair)
        signature = json.dumps(
            profile.configuration,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if signature in configurations:
            raise SettingsError("同じデータ準備設定を複数profileへ割り当てられません。")
        configurations.add(signature)
