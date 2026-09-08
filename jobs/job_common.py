# Databricks notebook source
"""Shared, server-side contracts for the RAG Lakeflow Jobs.

This file is both a Databricks Python notebook source (it can be used with
``%run``) and an importable Python module for local unit tests.  Physical
resource names live here and are never accepted as Job parameters.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote


CATALOG = "mikito_toyota_rag_eval"
SCHEMA = "rag_accuracy"
UC_PREFIX = f"{CATALOG}.{SCHEMA}"

PROJECTS_TABLE = f"{UC_PREFIX}.toyota_rag_projects"
DOCUMENTS_TABLE = f"{UC_PREFIX}.toyota_document_registry"
VEHICLE_MASTER_TABLE = f"{UC_PREFIX}.toyota_vehicle_master"
MODEL_CATALOG_TABLE = f"{UC_PREFIX}.toyota_rag_model_catalog"
MODEL_DEFAULTS_TABLE = f"{UC_PREFIX}.toyota_rag_model_defaults"
PARSED_TABLE = f"{UC_PREFIX}.toyota_parsed_v2"
VARIANTS_TABLE = f"{UC_PREFIX}.toyota_index_variants"
PREP_RUNS_TABLE = f"{UC_PREFIX}.toyota_rag_prep_runs"
EVAL_CASES_TABLE = f"{UC_PREFIX}.toyota_rag_eval_cases"
EVAL_RUNS_TABLE = f"{UC_PREFIX}.toyota_rag_eval_runs"
EVAL_RESULTS_TABLE = f"{UC_PREFIX}.toyota_rag_eval_results"
EVAL_SUGGESTIONS_TABLE = f"{UC_PREFIX}.toyota_rag_eval_suggestions"

BASELINE_PROJECT_ID = "1f113047-82b3-4327-b2b5-7322ecd26ad9"
BASELINE_PHYSICAL_VARIANT_ID = "baseline-standard-512-v1"
BASELINE_CHUNK_TABLE = f"{UC_PREFIX}.toyota_chunks_standard_512_v1"
BASELINE_INDEX_NAME = f"{UC_PREFIX}.toyota_chunks_standard_512_v1_index"
BASELINE_SEARCH_ENDPOINT = "toyota-rag-search"
BASELINE_EMBEDDING_MODEL_KEY = "emb-qwen3-0-6b"
BASELINE_EMBEDDING_ENDPOINT = "databricks-qwen3-embedding-0-6b"
BASELINE_INDEX_PROFILE_KEY = "baseline-standard-512-v1"
DEFAULT_EMBEDDING_FALLBACK_POLICY = (
    "READY_SELECTABLE_DISPLAY_NAME_MODEL_KEY_ASC"
)
STARTER_DATASET_VERSION = "starter-v1"
STARTER_DATASET_SPLIT = "development"
STARTER_QUESTION_TYPE = "starter_sample_unlabeled"
STARTER_EVAL_CASE_IDS = tuple(
    f"starter-sample-{slot:02d}" for slot in range(1, 4)
)
# Deployment-specific MLflow values must be configured on the evaluation Job.
# They intentionally have no source-code defaults: a copied Job must never
# write traces to a different Workspace resource by accident.
MLFLOW_EXPERIMENT_PATH = os.getenv("MLFLOW_EXPERIMENT_PATH", "").strip()
MLFLOW_EXPERIMENT_ID = os.getenv("MLFLOW_EXPERIMENT_ID", "").strip()
MLFLOW_TRACING_SQL_WAREHOUSE_ID = os.getenv(
    "MLFLOW_TRACING_SQL_WAREHOUSE_ID", ""
).strip()

BASELINE_DOCUMENT_IDS = frozenset(
    {
        "9ea03add-de22-4c9a-8cac-c8de8e234d6a",
        "dffd3261-c8b4-4a85-b1d6-a74576dbdb89",
        "00d0cbab-5813-45ea-a4e9-775e9056ad58",
        "1a6b0abc-e220-4dd5-85ba-1a6a07baee67",
        "031bab44-de2c-47d0-a382-8736da861416",
        "36c77b23-77d8-442d-9e1a-d3330e907490",
        "247c8253-4187-445b-bb75-1d0180e54830",
        "2715b88e-0c3a-4eec-ac4a-f5030b659761",
    }
)

PHASE_PRESETS: dict[str, dict[str, Any]] = {
    "phase_01": {
        "query_type": "ANN",
        "metadata_filtering": False,
        "reranking": False,
        "query_optimization": False,
    },
    "phase_02": {
        "query_type": "HYBRID",
        "metadata_filtering": False,
        "reranking": False,
        "query_optimization": False,
    },
    "phase_03": {
        "query_type": "HYBRID",
        "metadata_filtering": True,
        "reranking": False,
        "query_optimization": False,
    },
    "phase_04": {
        "query_type": "HYBRID",
        "metadata_filtering": True,
        "reranking": True,
        "query_optimization": False,
    },
    "phase_05": {
        "query_type": "HYBRID",
        "metadata_filtering": True,
        "reranking": True,
        "query_optimization": True,
    },
}

RETURN_COLUMNS = [
    "chunk_id",
    "project_id",
    "document_id",
    "chunk_to_retrieve",
    "parent_chunk_id",
    "parent_chunk_to_retrieve",
    "doc_uri",
    "page_number",
    "page_numbers",
    "parent_page_numbers",
    "title",
    "model",
    "model_year",
    "document_type",
    "vehicle_category",
    "variant_id",
]

_SAFE_ENDPOINT = re.compile(r"^[A-Za-z0-9_.-]{1,256}$")
_SAFE_MODEL_KEY = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SAFE_PROFILE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SAFE_UC_SEGMENT = re.compile(r"^[A-Za-z0-9_-]{1,255}$")
_SAFE_DATASET_VERSION = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")

CHUNK_SIZES = frozenset({256, 512, 1024})
CHUNK_METHODS = frozenset({"STANDARD", "SEMANTIC", "PARENT_CHILD"})
CONTENT_PROFILES = frozenset({"TEXT_ONLY", "LAYOUT_PRESERVING"})
OVERLAP_BY_SIZE = {256: 32, 512: 64, 1024: 128}
TOKENIZER_NAME = "deterministic_ja_mixed_v1"
INDEX_SYNC_COLUMNS = frozenset(
    {
        "project_id",
        "document_id",
        "chunk_to_retrieve",
        "parent_chunk_id",
        "parent_chunk_to_retrieve",
        "doc_uri",
        "page_number",
        "page_numbers",
        "parent_page_numbers",
        "title",
        "model",
        "model_year",
        "document_type",
        "vehicle_category",
        "section_title",
        "keywords",
        "variant_id",
    }
)
INDEX_PROFILE_FIELDS = frozenset(
    {
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
    }
)
INDEX_PROFILE_CONFIGURATION_FIELDS = (
    "chunk_method",
    "chunk_size_tokens",
    "parent_chunk_size_tokens",
    "content_profile",
    "cleaning_enabled",
    "semantic_metadata_enabled",
    "embedding_model_key",
)
_TOKEN_PATTERN = re.compile(
    r"[一-龯々〆ヵヶぁ-んァ-ヴー]|[A-Za-z0-9]+(?:[._:/-][A-Za-z0-9]+)*|[^\s]"
)


class JobContractError(ValueError):
    """Raised when a persisted run violates the server-side Job contract."""


def deep_dict(value: Any) -> Any:
    """Convert Spark Rows, SDK models, and nested containers to Python values."""

    if hasattr(value, "asDict"):
        return deep_dict(value.asDict(recursive=True))
    if hasattr(value, "as_dict"):
        return deep_dict(value.as_dict())
    if isinstance(value, Mapping):
        return {str(key): deep_dict(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [deep_dict(item) for item in value]
    return value


def require_uuid(value: str, field_name: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise JobContractError(f"{field_name} must be a UUID") from exc
    canonical = str(parsed)
    if str(value).lower() != canonical:
        raise JobContractError(f"{field_name} must use canonical UUID text")
    return canonical


def sql_string(value: str | None) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def sql_string_array(values: Sequence[str]) -> str:
    """Render a Spark SQL ARRAY literal using the same escaping as strings."""

    return "array(" + ",".join(sql_string(str(value)) for value in values) + ")"


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_json_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def parse_and_verify_config(
    config_text: str,
    expected_hash: str,
    *,
    ignored_hash_keys: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    try:
        parsed = json.loads(config_text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise JobContractError("persisted configuration is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise JobContractError("persisted configuration must be a JSON object")
    hash_payload = {
        key: value for key, value in parsed.items() if key not in ignored_hash_keys
    }
    actual_hash = canonical_json_hash(hash_payload)
    if actual_hash != str(expected_hash):
        raise JobContractError("persisted configuration hash mismatch")
    return parsed


def _qualified_uc_name(value: Any, field_name: str) -> str:
    """Validate one unquoted three-part UC name in the configured Schema."""

    text = str(value or "")
    parts = text.split(".")
    if (
        len(parts) != 3
        or any(not _SAFE_UC_SEGMENT.fullmatch(part) for part in parts)
        or parts[:2] != [CATALOG, SCHEMA]
    ):
        raise JobContractError(
            f"{field_name} must be an allow-listed three-part name in {UC_PREFIX}"
        )
    return text


def _fallback_index_profiles() -> list[dict[str, Any]]:
    """Return the one manually provisioned profile used by the basic hands-on."""

    return [
        {
            "key": BASELINE_INDEX_PROFILE_KEY,
            "source_table": BASELINE_CHUNK_TABLE,
            "index_name": BASELINE_INDEX_NAME,
            "search_endpoint": BASELINE_SEARCH_ENDPOINT,
            "chunk_method": "STANDARD",
            "chunk_size_tokens": 512,
            "parent_chunk_size_tokens": None,
            "content_profile": "LAYOUT_PRESERVING",
            "cleaning_enabled": True,
            "semantic_metadata_enabled": True,
            "embedding_model_key": BASELINE_EMBEDDING_MODEL_KEY,
            "embedding_endpoint": BASELINE_EMBEDDING_ENDPOINT,
        }
    ]


def load_index_profiles(raw_json: str | None = None) -> dict[str, dict[str, Any]]:
    """Load the exact allow-list of manually created source Table/Index pairs.

    Physical names are never derived from a request or a logical Variant UUID.
    ``RAG_INDEX_PROFILES_JSON`` can add administrator-provisioned comparison
    profiles; an unset variable intentionally exposes only the baseline pair.
    """

    raw = os.getenv("RAG_INDEX_PROFILES_JSON") if raw_json is None else raw_json
    if raw is None or not raw.strip():
        values: Any = _fallback_index_profiles()
    else:
        try:
            values = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise JobContractError("RAG_INDEX_PROFILES_JSON is not valid JSON") from exc
    if not isinstance(values, list) or not 1 <= len(values) <= 64:
        raise JobContractError("RAG_INDEX_PROFILES_JSON must contain 1 to 64 profiles")

    profiles: dict[str, dict[str, Any]] = {}
    resource_pairs: set[tuple[str, str]] = set()
    configuration_signatures: set[str] = set()
    for raw_profile in values:
        profile = deep_dict(raw_profile)
        if not isinstance(profile, dict) or set(profile) != INDEX_PROFILE_FIELDS:
            raise JobContractError("Index profile fields do not match the contract")
        key = str(profile.get("key") or "")
        if not _SAFE_PROFILE_KEY.fullmatch(key) or key in profiles:
            raise JobContractError("Index profile key is unsafe or duplicated")
        source_table = _qualified_uc_name(profile.get("source_table"), "source_table")
        index_name = _qualified_uc_name(profile.get("index_name"), "index_name")
        endpoint = validate_endpoint_name(str(profile.get("search_endpoint") or ""))
        embedding_endpoint = validate_endpoint_name(
            str(profile.get("embedding_endpoint") or "")
        )
        method = profile.get("chunk_method")
        size = profile.get("chunk_size_tokens")
        parent_size = profile.get("parent_chunk_size_tokens")
        if method not in CHUNK_METHODS or size not in CHUNK_SIZES:
            raise JobContractError("Index profile has an unsupported chunk method or size")
        if isinstance(size, bool):
            raise JobContractError("Index profile chunk size must be an integer")
        if method == "PARENT_CHILD":
            if (
                not isinstance(parent_size, int)
                or isinstance(parent_size, bool)
                or parent_size <= size
                or parent_size > 8192
            ):
                raise JobContractError("Index profile has an invalid parent chunk size")
        elif parent_size is not None:
            raise JobContractError("Index profile parent size is only valid for Parent-child")
        if profile.get("content_profile") not in CONTENT_PROFILES:
            raise JobContractError("Index profile has an unsupported content profile")
        for field in ("cleaning_enabled", "semantic_metadata_enabled"):
            if not isinstance(profile.get(field), bool):
                raise JobContractError(f"Index profile {field} must be boolean")
        model_key = str(profile.get("embedding_model_key") or "")
        if not _SAFE_MODEL_KEY.fullmatch(model_key):
            raise JobContractError("Index profile embedding model key is unsafe")

        normalized = {
            **profile,
            "key": key,
            "source_table": source_table,
            "index_name": index_name,
            "search_endpoint": endpoint,
            "embedding_model_key": model_key,
            "embedding_endpoint": embedding_endpoint,
        }
        pair = (source_table, index_name)
        signature = canonical_json(
            {field: normalized[field] for field in INDEX_PROFILE_CONFIGURATION_FIELDS}
        )
        if pair in resource_pairs:
            raise JobContractError("one source Table/Index pair is assigned more than once")
        if signature in configuration_signatures:
            raise JobContractError("one preparation configuration is assigned more than once")
        resource_pairs.add(pair)
        configuration_signatures.add(signature)
        profiles[key] = normalized
    return profiles


def resolve_index_profile(
    profile_key: str,
    configuration: Mapping[str, Any],
    *,
    profiles: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve and verify a server-selected pre-created resource profile."""

    if not _SAFE_PROFILE_KEY.fullmatch(str(profile_key or "")):
        raise JobContractError("index_profile_key is unsafe")
    allowed = load_index_profiles() if profiles is None else profiles
    raw_profile = allowed.get(str(profile_key))
    if raw_profile is None:
        raise JobContractError("index_profile_key is not allow-listed")
    profile = deep_dict(raw_profile)
    actual = deep_dict(configuration)
    expected = {
        field: profile.get(field) for field in INDEX_PROFILE_CONFIGURATION_FIELDS
    }
    if actual != expected:
        raise JobContractError(
            "preparation configuration does not match the selected existing Index profile"
        )
    return profile


def validate_variant_resource_pair(source_table: str, index_name: str) -> None:
    """Reject every physical pair not explicitly present in the administrator allow-list."""

    pair = (str(source_table), str(index_name))
    if pair not in {
        (str(profile["source_table"]), str(profile["index_name"]))
        for profile in load_index_profiles().values()
    }:
        raise JobContractError("Variant source Table/Index pair is not allow-listed")


def validate_endpoint_name(endpoint_name: str) -> str:
    if not _SAFE_ENDPOINT.fullmatch(endpoint_name):
        raise JobContractError("model catalog contains an unsafe endpoint name")
    return endpoint_name


def validate_existing_index(
    payload: Mapping[str, Any],
    *,
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate one manually created Index without accepting missing metadata."""

    value = deep_dict(payload)
    if not isinstance(value, dict):
        raise JobContractError("existing Index metadata is invalid")
    expected_name = str(profile["index_name"])
    expected_source = str(profile["source_table"])
    expected_search_endpoint = str(profile["search_endpoint"])
    expected_embedding_endpoint = str(profile["embedding_endpoint"])
    if value.get("name") != expected_name:
        raise JobContractError("existing Index name does not match the allow-listed name")
    if value.get("endpoint_name") != expected_search_endpoint:
        raise JobContractError("existing Index uses another AI Search endpoint")
    if str(value.get("primary_key") or "") != "chunk_id":
        raise JobContractError("existing Index primary key is not chunk_id")
    if str(value.get("index_type") or "").upper() != "DELTA_SYNC":
        raise JobContractError("existing Index is not DELTA_SYNC")
    if str(value.get("index_subtype") or "").upper() != "HYBRID":
        raise JobContractError("existing Index is not HYBRID")

    spec = value.get("delta_sync_index_spec")
    if not isinstance(spec, dict):
        raise JobContractError("existing Index Delta Sync specification is missing")
    if spec.get("source_table") != expected_source:
        raise JobContractError("existing Index points to another source table")
    if str(spec.get("pipeline_type") or "").upper() != "TRIGGERED":
        raise JobContractError("existing Index is not TRIGGERED")
    # The create request accepts ``columns_to_sync`` but the Index GET API can
    # omit it.  In that response shape, omission means the Index includes all
    # source-table columns; the later source-schema and search-manifest checks
    # still prove that every required retrieval column is usable.  When the
    # service does return an explicit list, keep the exact fail-closed check.
    returned_column_fields = [
        field for field in ("columns_to_sync", "columns_to_index") if field in spec
    ]
    if len(returned_column_fields) > 1:
        raise JobContractError(
            "existing Index synchronized columns do not match the contract"
        )
    if returned_column_fields:
        raw_columns = spec.get(returned_column_fields[0])
        if (
            not isinstance(raw_columns, list)
            or any(not isinstance(column, str) for column in raw_columns)
            or len(raw_columns) != len(set(raw_columns))
            or set(raw_columns) != set(INDEX_SYNC_COLUMNS)
        ):
            raise JobContractError(
                "existing Index synchronized columns do not match the contract"
            )
    embedding_columns = spec.get("embedding_source_columns")
    if not isinstance(embedding_columns, list):
        raise JobContractError("existing Index embedding source is missing")
    matches = [
        column
        for column in embedding_columns
        if isinstance(column, dict) and column.get("name") == "chunk_to_embed"
    ]
    if len(matches) != 1:
        raise JobContractError("existing Index embedding source is invalid")
    selected = matches[0]
    if selected.get("embedding_model_endpoint_name") != expected_embedding_endpoint:
        raise JobContractError("existing Index uses another Embedding endpoint")
    query_endpoint = selected.get("model_endpoint_name_for_query")
    if query_endpoint not in {None, expected_embedding_endpoint}:
        raise JobContractError("existing Index uses another query Embedding endpoint")
    return value


def select_default_model_key(
    rows: Sequence[Mapping[str, Any]],
    *,
    capability: str,
    preferred_model_key: str,
    fallback_policy: str = DEFAULT_EMBEDDING_FALLBACK_POLICY,
) -> str:
    """Resolve a preferred READY FMAPI model or a deterministic fallback.

    Endpoint discovery owns availability.  A stale preference therefore never
    makes an unavailable endpoint selectable, and the fallback order does not
    depend on Spark collection order.
    """

    if not _SAFE_MODEL_KEY.fullmatch(preferred_model_key):
        raise JobContractError("preferred model_key is unsafe")
    if fallback_policy != DEFAULT_EMBEDDING_FALLBACK_POLICY:
        raise JobContractError("unsupported model fallback policy")
    eligible: list[dict[str, Any]] = []
    for raw in rows:
        model = deep_dict(raw)
        capabilities = {str(item) for item in (model.get("capabilities") or [])}
        if (
            model.get("target_kind") == "FMAPI_ENDPOINT"
            and model.get("endpoint_state") == "READY"
            and model.get("selectable") is True
            and model.get("region_available") is True
            and capability in capabilities
        ):
            key = str(model.get("model_key") or "")
            if _SAFE_MODEL_KEY.fullmatch(key):
                eligible.append(model)
    if not eligible:
        raise JobContractError(f"no READY selectable {capability} model exists")
    if any(str(model["model_key"]) == preferred_model_key for model in eligible):
        return preferred_model_key
    return str(
        min(
            eligible,
            key=lambda model: (
                str(model.get("display_name") or "").casefold(),
                str(model.get("model_key") or ""),
            ),
        )["model_key"]
    )


def _normalized_question(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def build_starter_evaluation_cases(
    *,
    project_id: str,
    registry_rows: Sequence[Mapping[str, Any]],
    existing_cases: Sequence[Mapping[str, Any]],
    created_at: Any,
) -> list[dict[str, Any]]:
    """Build up to three safe, Project-scoped starter questions.

    These are deliberately *unlabelled*: a document URI is known, but an
    answer and relevant page are not invented.  Evaluation can still measure
    latency, groundedness, and citation correctness; reference-answer and
    page-level retrieval metrics remain NULL until a human supplies labels.
    Stable reserved IDs and normalized-question checks make retries idempotent.
    """

    canonical_project = require_uuid(project_id, "project_id")
    documents_by_id: dict[str, dict[str, Any]] = {}
    for raw in registry_rows:
        document = deep_dict(raw)
        if str(document.get("project_id") or canonical_project) != canonical_project:
            raise JobContractError("starter sample document escaped the selected Project")
        document_id = require_uuid(str(document.get("document_id") or ""), "document_id")
        doc_uri = str(document.get("doc_uri") or "").strip()
        if not doc_uri:
            raise JobContractError("starter sample document is missing doc_uri")
        documents_by_id.setdefault(document_id, document)
    documents = [documents_by_id[key] for key in sorted(documents_by_id)]
    if not documents:
        return []

    normalized_existing = [deep_dict(row) for row in existing_cases]
    for row in normalized_existing:
        case_id = str(row.get("eval_case_id") or "")
        if case_id in STARTER_EVAL_CASE_IDS and (
            str(row.get("dataset_version") or "") != STARTER_DATASET_VERSION
            or str(row.get("dataset_split") or "") != STARTER_DATASET_SPLIT
            or str(row.get("question_type") or "") != STARTER_QUESTION_TYPE
        ):
            raise JobContractError("reserved starter eval_case_id is used by another dataset")
    existing_ids = {
        str(row.get("eval_case_id") or "") for row in normalized_existing
    }
    existing_questions = {
        _normalized_question(row.get("question"))
        for row in normalized_existing
        if (
            str(row.get("dataset_version") or "") == STARTER_DATASET_VERSION
            and str(row.get("dataset_split") or "") == STARTER_DATASET_SPLIT
            and _normalized_question(row.get("question"))
        )
    }
    templates = (
        "「{title}」の概要を教えてください。",
        "「{title}」の重要なポイントを3つ教えてください。",
        "「{title}」に記載された主な手順や条件を教えてください。",
    )
    generated: list[dict[str, Any]] = []
    for index, (eval_case_id, template) in enumerate(
        zip(STARTER_EVAL_CASE_IDS, templates, strict=True)
    ):
        if eval_case_id in existing_ids:
            continue
        document = documents[index % len(documents)]
        title = str(
            document.get("title")
            or document.get("original_filename")
            or "PDF"
        ).strip()[:300]
        question = template.format(title=title)
        normalized = _normalized_question(question)
        if normalized in existing_questions:
            continue
        existing_questions.add(normalized)
        generated.append(
            {
                "project_id": canonical_project,
                "eval_case_id": eval_case_id,
                "question": question,
                "expected_answer": None,
                "relevant_doc_uri": str(document["doc_uri"]),
                "question_type": STARTER_QUESTION_TYPE,
                "is_answerable": True,
                "language": "ja",
                "dataset_version": STARTER_DATASET_VERSION,
                "dataset_split": STARTER_DATASET_SPLIT,
                "created_at": created_at,
            }
        )
    return generated


def evaluation_case_has_answer_reference(case: Mapping[str, Any]) -> bool:
    """Return whether answer correctness has a real reference label."""

    value = deep_dict(case)
    if str(value.get("expected_answer") or "").strip():
        return True
    return any(str(item).strip() for item in (value.get("expected_facts") or []))


def validate_preparation_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a UI preparation request without trusting any physical name."""

    row = deep_dict(record)
    require_uuid(str(row.get("prep_run_id", "")), "prep_run_id")
    require_uuid(str(row.get("target_variant_id", "")), "target_variant_id")
    require_uuid(str(row.get("project_id", "")), "project_id")
    if row.get("run_type") != "BUILD_VARIANT":
        raise JobContractError("unsupported preparation run_type")
    document_ids = row.get("document_ids")
    if not isinstance(document_ids, list) or not 1 <= len(document_ids) <= 100:
        raise JobContractError("preparation requires between 1 and 100 documents")
    canonical_document_ids = [
        require_uuid(str(value), "document_id") for value in document_ids
    ]
    if len(canonical_document_ids) != len(set(canonical_document_ids)):
        raise JobContractError("preparation document_ids must be unique")

    config = parse_and_verify_config(
        str(row.get("normalized_config_json", "")),
        str(row.get("config_hash", "")),
    )
    required_fields = {
        "run_type",
        "document_ids",
        "configuration",
        "index_profile_key",
    }
    current_fields = {*required_fields, "activate_on_success"}
    config_fields = set(config)
    if config_fields != required_fields and config_fields != current_fields:
        raise JobContractError("preparation configuration fields do not match the contract")
    if config.get("run_type") != "BUILD_VARIANT":
        raise JobContractError("configuration run_type does not match the row")
    if config.get("document_ids") != canonical_document_ids:
        raise JobContractError("configuration document_ids do not match the row")
    activate_on_success = config.get("activate_on_success", True)
    if not isinstance(activate_on_success, bool):
        raise JobContractError("activate_on_success must be boolean")
    # Old, already-persisted runs predate the server-only activation flag and
    # retain their historical True behavior. Deletion-created non-active
    # successors include False in the verified hash.
    configuration = config.get("configuration")
    if not isinstance(configuration, dict):
        raise JobContractError("configuration must be an object")
    required_keys = {
        "chunk_method",
        "chunk_size_tokens",
        "parent_chunk_size_tokens",
        "content_profile",
        "cleaning_enabled",
        "semantic_metadata_enabled",
        "embedding_model_key",
    }
    if set(configuration) != required_keys:
        raise JobContractError("configuration fields do not match the preparation contract")
    method = configuration.get("chunk_method")
    size = configuration.get("chunk_size_tokens")
    if method not in CHUNK_METHODS or size not in CHUNK_SIZES:
        raise JobContractError("unsupported chunk method or size")
    if configuration.get("content_profile") not in CONTENT_PROFILES:
        raise JobContractError("unsupported content profile")
    for field in ("cleaning_enabled", "semantic_metadata_enabled"):
        if not isinstance(configuration.get(field), bool):
            raise JobContractError(f"{field} must be boolean")
    parent_size = configuration.get("parent_chunk_size_tokens")
    if method == "PARENT_CHILD":
        if (
            not isinstance(parent_size, int)
            or isinstance(parent_size, bool)
            or parent_size <= size
            or parent_size > 8192
        ):
            raise JobContractError("Parent-child requires a parent size above child size and at most 8192")
    elif parent_size is not None:
        raise JobContractError("parent size is only valid for Parent-child")
    model_key = configuration.get("embedding_model_key")
    if not isinstance(model_key, str) or not _SAFE_MODEL_KEY.fullmatch(model_key):
        raise JobContractError("embedding model key is unsafe")
    profile_key = config.get("index_profile_key")
    if not isinstance(profile_key, str):
        raise JobContractError("index_profile_key must be a string")
    resolve_index_profile(profile_key, configuration)
    return config


def validate_registry_document_scope(
    *,
    project_id: str,
    requested_document_ids: Sequence[str],
    registry_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Fail closed when a requested document is missing or belongs elsewhere."""

    canonical_project = require_uuid(project_id, "project_id")
    requested = [require_uuid(str(value), "document_id") for value in requested_document_ids]
    if not requested or len(requested) != len(set(requested)):
        raise JobContractError("requested document_ids must be non-empty and unique")
    rows = [deep_dict(row) for row in registry_rows]
    row_ids = [str(row.get("document_id") or "") for row in rows]
    if (
        len(rows) != len(requested)
        or len(row_ids) != len(set(row_ids))
        or set(row_ids) != set(requested)
        or any(str(row.get("project_id") or "") != canonical_project for row in rows)
    ):
        raise JobContractError(
            "requested documents are missing, duplicated, or belong to another Project"
        )
    return rows


# Backward-compatible name retained for checked-in callers and tests.
validate_baseline_prep_record = validate_preparation_record


def estimate_token_count(text: str) -> int:
    """Deterministic Japanese/Latin token estimate with no external model dependency."""

    return sum(1 for _ in _TOKEN_PATTERN.finditer(text or ""))


def _split_text_to_limit(text: str, limit: int) -> list[str]:
    matches = list(_TOKEN_PATTERN.finditer(text or ""))
    if not matches:
        return []
    if len(matches) <= limit:
        return [text.strip()]
    pieces: list[str] = []
    for start in range(0, len(matches), limit):
        end = min(start + limit, len(matches))
        char_start = matches[start].start()
        char_end = matches[end - 1].end()
        piece = text[char_start:char_end].strip()
        if piece:
            pieces.append(piece)
    return pieces


def _format_element(element_type: str, content: str, description: str, profile: str) -> str:
    value = content.strip()
    description = description.strip()
    if not value and profile == "LAYOUT_PRESERVING":
        value = description
    if not value:
        return ""
    value = re.sub(r" {2,}", " ", value)
    if profile == "TEXT_ONLY":
        return value
    if element_type == "title":
        return "# " + value
    if element_type == "section_header":
        return "## " + value
    if element_type == "table":
        return "[表]\n" + value
    if element_type == "figure":
        suffix = "\n[図の説明] " + description if description and description != value else ""
        return "[図]\n" + value + suffix
    if element_type == "caption":
        return "[キャプション] " + value
    if element_type == "footnote":
        return "[注記] " + value
    return value


def prepare_chunk_units(
    elements: Sequence[Mapping[str, Any]],
    *,
    atomic_token_limit: int,
    cleaning_enabled: bool,
    content_profile: str,
) -> list[dict[str, Any]]:
    """Normalize parsed elements and split only elements above the child limit."""

    if atomic_token_limit <= 0 or content_profile not in CONTENT_PROFILES:
        raise JobContractError("invalid chunk-unit configuration")
    units: list[dict[str, Any]] = []
    current_section: str | None = None
    sorted_elements = sorted(
        (deep_dict(item) for item in elements),
        key=lambda item: (int(item.get("page_number") or 0), int(item.get("element_id") or 0)),
    )
    for element in sorted_elements:
        element_type = str(element.get("element_type") or "")
        if cleaning_enabled and element_type in {"page_header", "page_footer", "page_number"}:
            continue
        try:
            page_number = int(element.get("page_number"))
        except (TypeError, ValueError):
            continue
        if page_number < 1:
            continue
        content = str(element.get("content") or "")
        description = str(element.get("description") or "")
        text = _format_element(element_type, content, description, content_profile)
        if not text:
            continue
        boundary = element_type in {"title", "section_header"}
        if boundary:
            current_section = re.sub(r"^#+\s*", "", text).strip() or current_section
        pieces = _split_text_to_limit(text, atomic_token_limit)
        for position, piece in enumerate(pieces):
            units.append(
                {
                    "text": piece,
                    "token_count": estimate_token_count(piece),
                    "page_numbers": [page_number],
                    "section_title": current_section,
                    "boundary": boundary and position == 0,
                }
            )
    return units


def _unit_pages(units: Sequence[Mapping[str, Any]]) -> list[int]:
    pages: list[int] = []
    for unit in units:
        for page in unit.get("page_numbers") or []:
            value = int(page)
            if value not in pages:
                pages.append(value)
    return sorted(pages)


def _unit_sections(units: Sequence[Mapping[str, Any]]) -> list[str]:
    sections: list[str] = []
    for unit in units:
        section = str(unit.get("section_title") or "").strip()
        if section and section not in sections:
            sections.append(section)
    return sections


def _overlap_tail(units: Sequence[Mapping[str, Any]], overlap_tokens: int) -> list[dict[str, Any]]:
    if overlap_tokens <= 0:
        return []
    result: list[dict[str, Any]] = []
    tokens = 0
    for raw_unit in reversed(units):
        unit = dict(raw_unit)
        count = int(unit["token_count"])
        if tokens + count > overlap_tokens:
            remaining = overlap_tokens - tokens
            if remaining > 0:
                matches = list(_TOKEN_PATTERN.finditer(str(unit["text"])))
                if matches:
                    selected = matches[max(0, len(matches) - remaining):]
                    suffix = str(unit["text"])[selected[0].start():selected[-1].end()].strip()
                    if suffix:
                        partial = dict(unit)
                        partial["text"] = suffix
                        partial["token_count"] = estimate_token_count(suffix)
                        partial["boundary"] = False
                        result.append(partial)
            break
        result.append(unit)
        tokens += count
    return list(reversed(result))


def pack_chunk_units(
    units: Sequence[Mapping[str, Any]],
    *,
    target_tokens: int,
    overlap_tokens: int,
    semantic_boundaries: bool,
    page_boundary_ratio: float = 0.75,
) -> list[list[dict[str, Any]]]:
    """Pack atomic elements with bounded overlap and optional section preference."""

    if target_tokens <= 0 or not 0 <= overlap_tokens < target_tokens:
        raise JobContractError("invalid chunk packing limits")
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_tokens = 0

    def flush(*, allow_overlap: bool) -> None:
        nonlocal current, current_tokens
        if not current:
            return
        previous = current
        chunks.append(previous)
        current = _overlap_tail(previous, overlap_tokens) if allow_overlap else []
        current_tokens = sum(int(item["token_count"]) for item in current)

    for raw_unit in units:
        unit = dict(raw_unit)
        unit_tokens = int(unit.get("token_count") or 0)
        if not unit.get("text") or unit_tokens <= 0 or unit_tokens > target_tokens:
            raise JobContractError("atomic chunk unit violates the token boundary")
        page_transition = bool(
            current
            and set(unit.get("page_numbers") or []).isdisjoint(_unit_pages(current))
        )
        if semantic_boundaries and unit.get("boundary") and current:
            flush(allow_overlap=False)
        elif (
            page_transition
            and current_tokens >= int(target_tokens * page_boundary_ratio)
        ):
            flush(allow_overlap=True)
        if current and current_tokens + unit_tokens > target_tokens:
            flush(allow_overlap=True)
        if current and current_tokens + unit_tokens > target_tokens:
            # A large next unit can leave no room for the overlap tail.
            current = []
            current_tokens = 0
        current.append(unit)
        current_tokens += unit_tokens
    if current:
        chunks.append(current)
    return chunks


def _chunk_text(units: Sequence[Mapping[str, Any]]) -> str:
    return "\n\n".join(str(unit["text"]).strip() for unit in units if str(unit.get("text") or "").strip())


def _stable_chunk_id(*parts: Any) -> str:
    return hashlib.sha256("||".join(str(part) for part in parts).encode("utf-8")).hexdigest()


def _embedding_text(
    text: str,
    document: Mapping[str, Any],
    pages: Sequence[int],
    sections: Sequence[str],
    semantic_metadata_enabled: bool,
) -> str:
    if not semantic_metadata_enabled:
        return text
    context = [f"文書タイトル: {document.get('title') or ''}"]
    generic_fields = (
        ("カテゴリ", document.get("category")),
        ("文書日付", document.get("document_date")),
        ("出典", document.get("source")),
        ("車種", document.get("model")),
        ("年式", document.get("model_year")),
        ("文書種別", document.get("document_type")),
        ("車両カテゴリ", document.get("vehicle_category")),
    )
    context.extend(
        f"{label}: {value}" for label, value in generic_fields
        if value is not None and str(value).strip()
    )
    tags = document.get("tags") or []
    if isinstance(tags, Sequence) and not isinstance(tags, (str, bytes, bytearray)):
        tag_values = [str(value).strip() for value in tags if str(value).strip()]
        if tag_values:
            context.append("タグ: " + ", ".join(tag_values))
    stored_metadata = document.get("metadata_json")
    if isinstance(stored_metadata, str) and stored_metadata:
        try:
            stored_metadata = json.loads(stored_metadata)
        except json.JSONDecodeError:
            stored_metadata = {}
    custom_metadata = (
        stored_metadata.get("custom") if isinstance(stored_metadata, Mapping) else {}
    )
    if isinstance(custom_metadata, Mapping):
        remaining = 4000
        for key, value in sorted(custom_metadata.items(), key=lambda item: str(item[0]).casefold()):
            line = f"{str(key).strip()}: {str(value).strip()}"[:1000]
            if not line.strip(": ") or remaining <= 0:
                continue
            context.append("メタデータ: " + line[:remaining])
            remaining -= len(line)
    context.append("物理ページ: " + ",".join(str(page) for page in pages))
    summary = str(document.get("summary") or "").strip()
    if summary:
        context.append("文書概要: " + summary)
    if sections:
        context.append("セクション: " + " / ".join(sections))
    return "\n".join([*context, "", text])


def build_document_chunks(
    document: Mapping[str, Any],
    elements: Sequence[Mapping[str, Any]],
    configuration: Mapping[str, Any],
    *,
    project_id: str,
    variant_id: str,
) -> list[dict[str, Any]]:
    """Build one document's Standard, Semantic, or Parent-child chunks."""

    require_uuid(project_id, "project_id")
    require_uuid(variant_id, "variant_id")
    document = deep_dict(document)
    configuration = deep_dict(configuration)
    method = str(configuration["chunk_method"])
    size = int(configuration["chunk_size_tokens"])
    overlap = OVERLAP_BY_SIZE[size]
    units = prepare_chunk_units(
        elements,
        atomic_token_limit=size - overlap,
        cleaning_enabled=bool(configuration["cleaning_enabled"]),
        content_profile=str(configuration["content_profile"]),
    )
    if not units:
        raise JobContractError(f"document {document.get('document_id')} produced no chunk units")

    output: list[dict[str, Any]] = []
    if method in {"STANDARD", "SEMANTIC"}:
        packed = pack_chunk_units(
            units,
            target_tokens=size,
            overlap_tokens=overlap,
            semantic_boundaries=method == "SEMANTIC",
        )
        for ordinal, chunk_units in enumerate(packed, start=1):
            text = _chunk_text(chunk_units)
            pages = _unit_pages(chunk_units)
            sections = _unit_sections(chunk_units)
            chunk_id = _stable_chunk_id(variant_id, document["document_id"], "chunk", ordinal, text)
            output.append(
                {
                    "chunk_id": chunk_id,
                    "chunk_to_retrieve": text,
                    "chunk_to_embed": _embedding_text(
                        text, document, pages, sections,
                        bool(configuration["semantic_metadata_enabled"]),
                    ),
                    "page_numbers": pages,
                    "section_title": " / ".join(sections) or None,
                    "keywords": sections if configuration["semantic_metadata_enabled"] else [],
                    "parent_chunk_id": None,
                    "parent_chunk_to_retrieve": text,
                    "parent_page_numbers": pages,
                }
            )
    elif method == "PARENT_CHILD":
        parent_size = int(configuration["parent_chunk_size_tokens"])
        parents = pack_chunk_units(
            units,
            target_tokens=parent_size,
            overlap_tokens=0,
            semantic_boundaries=False,
        )
        child_ordinal = 0
        for parent_ordinal, parent_units in enumerate(parents, start=1):
            parent_text = _chunk_text(parent_units)
            parent_pages = _unit_pages(parent_units)
            parent_id = _stable_chunk_id(
                variant_id, document["document_id"], "parent", parent_ordinal, parent_text
            )
            children = pack_chunk_units(
                parent_units,
                target_tokens=size,
                overlap_tokens=overlap,
                semantic_boundaries=False,
            )
            for child_units in children:
                child_ordinal += 1
                text = _chunk_text(child_units)
                pages = _unit_pages(child_units)
                if not set(pages).issubset(parent_pages):
                    raise JobContractError("child page range is outside its parent")
                sections = _unit_sections(child_units)
                chunk_id = _stable_chunk_id(
                    variant_id, document["document_id"], "child", child_ordinal, text
                )
                output.append(
                    {
                        "chunk_id": chunk_id,
                        "chunk_to_retrieve": text,
                        "chunk_to_embed": _embedding_text(
                            text, document, pages, sections,
                            bool(configuration["semantic_metadata_enabled"]),
                        ),
                        "page_numbers": pages,
                        "section_title": " / ".join(sections) or None,
                        "keywords": sections if configuration["semantic_metadata_enabled"] else [],
                        "parent_chunk_id": parent_id,
                        "parent_chunk_to_retrieve": parent_text,
                        "parent_page_numbers": parent_pages,
                    }
                )
    else:
        raise JobContractError("unsupported chunk method")

    for row in output:
        if not row["chunk_to_retrieve"] or estimate_token_count(row["chunk_to_retrieve"]) > size:
            raise JobContractError("generated child chunk exceeds the configured token boundary")
        if not set(row["page_numbers"]).issubset(set(row["parent_page_numbers"])):
            raise JobContractError("generated child pages are not contained by parent pages")
    return output


def validate_eval_batch(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate that all Phase rows form one immutable evaluation batch."""

    if not rows:
        raise JobContractError("evaluation run was not found")
    normalized = [deep_dict(row) for row in rows]
    first = normalized[0]
    eval_run_id = require_uuid(str(first.get("eval_run_id", "")), "eval_run_id")
    project_id = require_uuid(str(first.get("project_id", "")), "project_id")

    invariant_fields = (
        "project_id",
        "eval_run_id",
        "variant_id",
        "dataset_version",
        "dataset_split",
        "answer_model_key",
        "judge_model_key",
        "query_optimizer_model_key",
        "advisor_model_key",
        "trial_count",
        "config_json",
        "config_hash",
    )
    for row in normalized:
        if any(row.get(field) != first.get(field) for field in invariant_fields):
            raise JobContractError("evaluation Phase rows do not share one batch configuration")
        if row.get("eval_run_id") != eval_run_id:
            raise JobContractError("evaluation row ID is not canonical")

    phases = [str(row.get("phase_id")) for row in normalized]
    if any(phase not in PHASE_PRESETS for phase in phases):
        raise JobContractError("evaluation Phase rows are missing or unsupported")
    # Concurrent insert-only Delta MERGEs can materialize identical control
    # rows because Delta tables do not enforce a unique key.  The invariant
    # checks above reject conflicting duplicates.  Identical Phase replicas
    # are therefore collapsed here; the Lakeflow idempotency token still
    # guarantees that only one evaluation Job runs.
    phases = sorted(set(phases))
    config = parse_and_verify_config(
        str(first["config_json"]),
        str(first["config_hash"]),
        ignored_hash_keys=frozenset({"_idempotency_key"}),
    )
    expected_config_fields = {
        "phase_ids",
        "trial_count",
        "dataset_version",
        "dataset_split",
        "evaluation_case_ids",
        "variant_id",
        "answer_model_key",
        "judge_model_key",
        "final_k",
    }
    actual_config_fields = frozenset(set(config) - {"_idempotency_key"})
    legacy_config_fields = expected_config_fields - {"evaluation_case_ids"}
    if actual_config_fields not in {frozenset(expected_config_fields), frozenset(legacy_config_fields)}:
        raise JobContractError("evaluation configuration fields do not match the contract")
    selected_case_ids = config.get("evaluation_case_ids")
    if selected_case_ids is not None:
        if (
            not isinstance(selected_case_ids, list)
            or not selected_case_ids
            or len(selected_case_ids) > 1000
            or any(not isinstance(item, str) or not item.strip() for item in selected_case_ids)
            or len(selected_case_ids) != len(set(selected_case_ids))
        ):
            raise JobContractError("evaluation_case_ids is empty, duplicated, or invalid")
    configured_phases = config.get("phase_ids")
    if not isinstance(configured_phases, list) or set(configured_phases) != set(phases):
        raise JobContractError("configuration phase_ids do not match persisted Phase rows")
    comparisons = {
        "trial_count": first.get("trial_count"),
        "dataset_version": first.get("dataset_version"),
        "dataset_split": first.get("dataset_split"),
        "variant_id": first.get("variant_id"),
        "answer_model_key": first.get("answer_model_key"),
        "judge_model_key": first.get("judge_model_key"),
    }
    for field, expected in comparisons.items():
        if config.get(field) != expected:
            raise JobContractError(f"configuration {field} does not match persisted rows")
    trial_count = first.get("trial_count")
    if not isinstance(trial_count, int) or isinstance(trial_count, bool) or not 1 <= trial_count <= 10:
        raise JobContractError("trial_count must be between 1 and 10")
    if config.get("final_k") != 10:
        raise JobContractError("this evaluation schema requires final_k=10")
    if first.get("dataset_split") not in {"development", "holdout"}:
        raise JobContractError("unsupported dataset_split")
    dataset_version = first.get("dataset_version")
    if not isinstance(dataset_version, str) or not _SAFE_DATASET_VERSION.fullmatch(dataset_version):
        raise JobContractError("dataset_version is unsafe")
    for field in ("answer_model_key", "judge_model_key"):
        value = first.get(field)
        if not isinstance(value, str) or not _SAFE_MODEL_KEY.fullmatch(value):
            raise JobContractError(f"{field} is unsafe")
    return {
        **first,
        "project_id": project_id,
        "config": config,
        "phases": phases,
    }


def validate_eval_cases(
    rows: Sequence[Mapping[str, Any]],
    *,
    project_id: str,
    dataset_version: str,
    dataset_split: str,
    expected_count: int | None = None,
) -> list[dict[str, Any]]:
    """Validate the Project-scoped evaluation dataset selected by a run."""

    canonical_project = require_uuid(project_id, "project_id")
    cases = [deep_dict(row) for row in rows]
    if not cases:
        raise JobContractError(
            "no evaluation cases exist for the selected Project, dataset version, and split"
        )
    case_ids = [str(case.get("eval_case_id") or "") for case in cases]
    if not all(case_ids) or len(case_ids) != len(set(case_ids)):
        raise JobContractError("evaluation cases contain a missing or duplicated eval_case_id")
    if expected_count is not None and len(cases) != expected_count:
        raise JobContractError("the fixed baseline evaluation case set is incomplete")
    if any(
        str(case.get("project_id") or "") != canonical_project
        or str(case.get("dataset_version") or "") != dataset_version
        or str(case.get("dataset_split") or "") != dataset_split
        for case in cases
    ):
        raise JobContractError("evaluation cases escaped the selected Project or dataset")
    for case in cases:
        if str(case.get("question_type") or "") != STARTER_QUESTION_TYPE:
            continue
        if (
            evaluation_case_has_answer_reference(case)
            or list(case.get("relevant_pages") or [])
            or list(case.get("relevance_judgments") or [])
            or not str(case.get("relevant_doc_uri") or "").strip()
        ):
            raise JobContractError(
                "starter sample must keep unknown answer/page labels empty and reference one Project document"
            )
    return cases


def validate_eval_case_document_scope(
    rows: Sequence[Mapping[str, Any]],
    *,
    allowed_doc_uris: set[str],
) -> None:
    """Ensure qrels and direct document labels stay inside the Project corpus."""

    for raw in rows:
        case = deep_dict(raw)
        referenced: set[str] = set()
        direct = case.get("relevant_doc_uri")
        if direct:
            referenced.add(str(direct))
        for judgment in case.get("relevance_judgments") or []:
            item = deep_dict(judgment)
            if item.get("doc_uri"):
                referenced.add(str(item["doc_uri"]))
        if not referenced.issubset(allowed_doc_uris):
            raise JobContractError(
                "evaluation ground truth references a document outside the selected Project"
            )


def resolve_project_variant_id(
    *,
    project_id: str,
    requested_variant_id: str,
    active_variant_id: str | None,
) -> str:
    """Resolve ``default`` within one Project without cross-Project fallback."""

    canonical_project = require_uuid(project_id, "project_id")
    resolved = active_variant_id if requested_variant_id == "default" else requested_variant_id
    if not resolved:
        raise JobContractError("the selected Project has no active Index Variant")
    if resolved == BASELINE_PHYSICAL_VARIANT_ID:
        if canonical_project != BASELINE_PROJECT_ID:
            raise JobContractError("the baseline Index Variant belongs to another Project")
        return resolved
    return require_uuid(str(resolved), "variant_id")


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(
            r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.IGNORECASE
        )
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def parse_model_text(raw: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    value = deep_dict(raw)
    usage = value.get("usage") if isinstance(value.get("usage"), dict) else {}
    choices = value.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        content = message.get("content") or choice.get("text")
        if isinstance(content, str) and content.strip():
            return content.strip(), usage
    for key in ("output_text", "answer", "text"):
        if isinstance(value.get(key), str) and value[key].strip():
            return value[key].strip(), usage
    predictions = value.get("predictions")
    if isinstance(predictions, list) and predictions:
        candidate = predictions[0]
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip(), usage
        if isinstance(candidate, dict):
            for key in ("content", "text", "answer"):
                if isinstance(candidate.get(key), str) and candidate[key].strip():
                    return candidate[key].strip(), usage
    raise JobContractError("model response did not contain text")


def endpoint_path(endpoint_name: str) -> str:
    if not _SAFE_ENDPOINT.fullmatch(endpoint_name):
        raise JobContractError("model catalog contains an unsafe endpoint name")
    return f"/serving-endpoints/{quote(endpoint_name, safe='')}/invocations"


def extract_validated_filters(
    question: str,
    vehicle_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Extract conservative filters and validate years against vehicle master data."""

    folded = question.casefold()
    matching: list[dict[str, Any]] = []
    for raw_row in vehicle_rows:
        row = deep_dict(raw_row)
        aliases = row.get("aliases") or []
        names = [row.get("model"), *aliases]
        if any(str(name).casefold() in folded for name in names if name):
            matching.append(row)

    result: dict[str, Any] = {}
    unique_models = {str(row["model"]) for row in matching if row.get("model")}
    if len(unique_models) == 1:
        model = next(iter(unique_models))
        result["model"] = model
        model_row = next(row for row in matching if str(row.get("model")) == model)
        years = {int(year) for year in (model_row.get("valid_model_years") or [])}
        mentioned_years = {
            int(value) for value in re.findall(r"(?<!\d)(?:19\d{2}|20\d{2}|2100)(?!\d)", question)
        }
        if len(mentioned_years) == 1:
            year = next(iter(mentioned_years))
            if year in years:
                result["model_year"] = year

    # Only high-confidence document-type phrases are mapped.  This avoids
    # leaking expected_filter labels from the evaluation data into retrieval.
    if re.search(r"(?:グレード|標準装備|オプション|装着条件|装備差)", question):
        result["document_type"] = "equipment_spec"
    elif re.search(r"(?:レスキュー|救援|高電圧|サービスプラグ)", question):
        result["document_type"] = "emergency_response_guide"
    elif "安全センサー図" in question:
        result["document_type"] = "safety_operation_guide"
    return result


_LEGACY_FILTER_FIELDS = (
    "model",
    "model_year",
    "document_type",
    "vehicle_category",
)


def _stored_registry_metadata(value: Any) -> dict[str, Any]:
    """Read the versioned metadata envelope without trusting its shape."""

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    value = deep_dict(value)
    return value if isinstance(value, dict) else {}


def _registry_value(row: Mapping[str, Any], field: str) -> Any:
    """Prefer typed registry columns and fall back to the JSON envelope."""

    value = row.get(field)
    if value is not None and str(value).strip():
        return value
    stored = _stored_registry_metadata(row.get("metadata_json"))
    section_name = "legacy" if field in _LEGACY_FILTER_FIELDS else "common"
    section = stored.get(section_name)
    return section.get(field) if isinstance(section, Mapping) else None


def _registry_tags(row: Mapping[str, Any]) -> list[str]:
    value = row.get("tags")
    if value is None:
        stored = _stored_registry_metadata(row.get("metadata_json"))
        common = stored.get("common")
        value = common.get("tags") if isinstance(common, Mapping) else None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _metadata_text(value: Any) -> str:
    if hasattr(value, "isoformat"):
        try:
            return str(value.isoformat()).strip()
        except (TypeError, ValueError):
            pass
    return str(value).strip()


def _metadata_value_is_mentioned(value: str, question: str) -> bool:
    """Match a persisted value conservatively in a case-folded question."""

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


def _metadata_values_equal(field: str, actual: Any, expected: Any) -> bool:
    if field == "model_year":
        try:
            return int(actual) == int(expected)
        except (TypeError, ValueError):
            return False
    return _metadata_text(actual).casefold() == _metadata_text(expected).casefold()


def _vehicle_master_names(row: Mapping[str, Any]) -> set[str]:
    normalized = deep_dict(row)
    aliases = normalized.get("aliases") or []
    if not isinstance(aliases, Sequence) or isinstance(
        aliases, (str, bytes, bytearray)
    ):
        aliases = []
    return {
        _metadata_text(value).casefold()
        for value in [normalized.get("model"), *aliases]
        if value is not None and _metadata_text(value)
    }


def _project_legacy_value_matches(
    field: str,
    actual: Any,
    expected: Any,
    vehicle_rows: Sequence[Mapping[str, Any]],
) -> bool:
    if field != "model":
        return _metadata_values_equal(field, actual, expected)
    folded_actual = _metadata_text(actual).casefold()
    folded_expected = _metadata_text(expected).casefold()
    for raw in vehicle_rows:
        row = deep_dict(raw)
        if _metadata_text(row.get("model")).casefold() != folded_expected:
            continue
        return folded_actual in _vehicle_master_names(row)
    return folded_actual == folded_expected


def extract_project_semantic_filters(
    question: str,
    registry_rows: Sequence[Mapping[str, Any]],
    vehicle_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Extract legacy Toyota labels only when the Project registry supports them.

    ``extract_validated_filters`` remains the semantic extraction contract used
    by the seeded Toyota evaluation labels.  Scoping the vehicle master to
    models actually present in the selected Project prevents ordinary terms
    such as ``Crown`` in a non-vehicle corpus from becoming vehicle filters.
    """

    rows = [deep_dict(row) for row in registry_rows]
    project_models = {
        _metadata_text(_registry_value(row, "model")).casefold()
        for row in rows
        if _registry_value(row, "model") is not None
        and _metadata_text(_registry_value(row, "model"))
    }
    scoped_vehicle_rows = [
        deep_dict(row)
        for row in vehicle_rows
        if _vehicle_master_names(row).intersection(project_models)
    ]
    extracted = extract_validated_filters(question, scoped_vehicle_rows)
    # A master row alone never authorizes a filter.  Each extracted scalar must
    # also occur in a document belonging to the selected Project/Variant.
    return {
        field: value
        for field, value in extracted.items()
        if field in _LEGACY_FILTER_FIELDS
        and any(
            _project_legacy_value_matches(
                field,
                _registry_value(row, field),
                value,
                scoped_vehicle_rows,
            )
            for row in rows
            if _registry_value(row, field) is not None
        )
    }


def resolve_project_filter_document_ids(
    question: str,
    registry_rows: Sequence[Mapping[str, Any]],
    semantic_filters: Mapping[str, Any] | None = None,
    vehicle_rows: Sequence[Mapping[str, Any]] = (),
) -> list[str]:
    """Resolve generic and legacy metadata to safe Project document IDs.

    AI Search never receives an arbitrary JSON path or an unverified metadata
    value.  Metadata is evaluated against the selected Project/Variant's
    registry rows, and only the universally indexed scalar ``document_id`` is
    returned.  Values within one field are ORed; different fields are ANDed.
    Empty/conflicting/full-corpus selections deliberately fall back to an
    unfiltered query so metadata extraction cannot hide every source.
    """

    folded_question = (question or "").casefold()
    rows = [deep_dict(row) for row in registry_rows]
    all_ids = {
        str(row.get("document_id"))
        for row in rows
        if row.get("document_id")
    }
    if len(all_ids) <= 1:
        return []

    buckets: dict[tuple[str, str], set[str]] = {}
    custom_display_keys: dict[tuple[str, str], str] = {}
    for row in rows:
        document_id = str(row.get("document_id") or "")
        if not document_id:
            continue

        def add(field: str, value: Any, *, custom_key: str = "") -> None:
            if value is None:
                return
            text_value = _metadata_text(value)
            if not text_value:
                return
            bucket_key = (field, text_value.casefold())
            buckets.setdefault(bucket_key, set()).add(document_id)
            if custom_key:
                custom_display_keys[bucket_key] = custom_key

        for field in ("category", "source", "document_date"):
            add(field, _registry_value(row, field))
        for tag in _registry_tags(row):
            add("tags", tag)
        stored = _stored_registry_metadata(row.get("metadata_json"))
        custom = stored.get("custom")
        if isinstance(custom, Mapping):
            for key, value in custom.items():
                if isinstance(key, str) and isinstance(value, str):
                    add("custom:" + key.casefold(), value, custom_key=key)

    matched_buckets: dict[str, list[tuple[str, set[str]]]] = {}
    for bucket_key, document_ids in buckets.items():
        field, folded_value = bucket_key
        if document_ids == all_ids or not _metadata_value_is_mentioned(
            folded_value, folded_question
        ):
            continue
        if field.startswith("custom:"):
            folded_key = custom_display_keys.get(bucket_key, "").casefold()
            if not folded_key or not _metadata_value_is_mentioned(
                folded_key, folded_question
            ):
                continue
        matched_buckets.setdefault(field, []).append((folded_value, document_ids))

    constraints: dict[str, set[str]] = {}
    for field, candidates in matched_buckets.items():
        # Prefer the longest/specific value when another matched value is its
        # substring (for example 社内公開 over 公開).
        specific = [
            (value, document_ids)
            for value, document_ids in candidates
            if not any(value != other and value in other for other, _ in candidates)
        ]
        for _, document_ids in specific:
            constraints.setdefault(field, set()).update(document_ids)

    for field, expected in (semantic_filters or {}).items():
        if field not in _LEGACY_FILTER_FIELDS or expected is None:
            continue
        matching_ids = {
            str(row.get("document_id"))
            for row in rows
            if row.get("document_id")
            and _registry_value(row, field) is not None
            and _project_legacy_value_matches(
                field,
                _registry_value(row, field),
                expected,
                vehicle_rows,
            )
        }
        if matching_ids and matching_ids != all_ids:
            constraints[field] = matching_ids

    if not constraints:
        return []
    candidate_ids = set(all_ids)
    for document_ids in constraints.values():
        candidate_ids.intersection_update(document_ids)
    if not candidate_ids or candidate_ids == all_ids or len(candidate_ids) > 100:
        return []
    return sorted(candidate_ids)


def resolve_project_search_filters(
    question: str,
    registry_rows: Sequence[Mapping[str, Any]],
    vehicle_rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the physical AI Search filter and legacy evaluation labels."""

    semantic_filters = extract_project_semantic_filters(
        question,
        registry_rows,
        vehicle_rows,
    )
    document_ids = resolve_project_filter_document_ids(
        question,
        registry_rows,
        semantic_filters,
        vehicle_rows,
    )
    search_filters = {"document_id": document_ids} if document_ids else {}
    return search_filters, semantic_filters


def merge_project_variant_filters(
    *,
    project_id: str,
    variant_id: str,
    metadata_filters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Add non-optional tenant filters to every shared-Index query."""

    canonical_project = require_uuid(project_id, "project_id")
    resolved_variant = str(variant_id or "")
    if resolved_variant == BASELINE_PHYSICAL_VARIANT_ID:
        if canonical_project != BASELINE_PROJECT_ID:
            raise JobContractError("the baseline Index Variant belongs to another Project")
    else:
        resolved_variant = require_uuid(resolved_variant, "variant_id")
    optional = deep_dict(metadata_filters or {})
    if not isinstance(optional, dict):
        raise JobContractError("metadata filters must be an object")
    if {"project_id", "variant_id"}.intersection(optional):
        raise JobContractError("metadata filters cannot override Project or Variant scope")
    return {
        "project_id": canonical_project,
        "variant_id": resolved_variant,
        **optional,
    }


def filter_exact_match(actual: Mapping[str, Any], expected: Any) -> bool | None:
    if expected is None:
        return None
    expected_dict = deep_dict(expected)
    if not isinstance(expected_dict, dict):
        return None
    expected_dict = {key: value for key, value in expected_dict.items() if value is not None}
    if not expected_dict:
        return None
    return dict(actual) == expected_dict


def _to_int_list(value: Any) -> list[int]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    result: list[int] = []
    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def row_pages(row: Mapping[str, Any]) -> list[int]:
    for field in ("parent_page_numbers", "page_numbers"):
        pages = _to_int_list(row.get(field))
        if pages:
            return list(dict.fromkeys(pages))
    try:
        return [int(row.get("page_number"))]
    except (TypeError, ValueError):
        return []


def page_metrics(
    relevance_judgments: Sequence[Mapping[str, Any]],
    retrieved_rows: Sequence[Mapping[str, Any]],
    *,
    k: int = 10,
    exclude: bool = False,
) -> dict[str, float | None]:
    """Compute graded, page-level Recall/Precision/DCG/nDCG at ``k`` pages."""

    qrel_by_page: dict[tuple[str, int], int] = {}
    for raw in relevance_judgments or []:
        judgment = deep_dict(raw)
        try:
            key = (str(judgment["doc_uri"]), int(judgment["page_number"]))
            grade = max(0, int(judgment["relevance_grade"]))
        except (KeyError, TypeError, ValueError):
            continue
        qrel_by_page[key] = max(qrel_by_page.get(key, 0), grade)
    if exclude or not qrel_by_page:
        return {
            "recall": None,
            "precision": None,
            "dcg": None,
            "ndcg": None,
            "chunk_page_coverage_recall": None,
        }

    ranked_pages: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    chunk_coverage: set[tuple[str, int]] = set()
    for raw_row in retrieved_rows[:k]:
        row = deep_dict(raw_row)
        uri = str(row.get("doc_uri") or "")
        for page in row_pages(row):
            key = (uri, page)
            chunk_coverage.add(key)
            if key not in seen:
                seen.add(key)
                ranked_pages.append(key)
    ranked_pages = ranked_pages[:k]
    relevant = {key for key, grade in qrel_by_page.items() if grade > 0}
    hits = len(relevant.intersection(ranked_pages))
    dcg = sum(
        ((2 ** qrel_by_page.get(key, 0)) - 1) / math.log2(rank + 1)
        for rank, key in enumerate(ranked_pages, start=1)
    )
    ideal_grades = sorted(qrel_by_page.values(), reverse=True)[:k]
    ideal_dcg = sum(
        ((2**grade) - 1) / math.log2(rank + 1)
        for rank, grade in enumerate(ideal_grades, start=1)
    )
    return {
        "recall": hits / len(relevant),
        "precision": hits / k,
        "dcg": dcg,
        "ndcg": dcg / ideal_dcg if ideal_dcg else None,
        "chunk_page_coverage_recall": len(relevant.intersection(chunk_coverage)) / len(relevant),
    }


def parse_search_response(raw: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    value = deep_dict(raw)
    manifest = value.get("manifest") or {}
    result = value.get("result") or {}
    columns = manifest.get("columns") or (manifest.get("schema") or {}).get("columns") or []
    names = [column.get("name") for column in columns if isinstance(column, dict)]
    arrays = result.get("data_array") or []
    if not names:
        raise JobContractError("AI Search response did not contain a column manifest")
    required = {
        "chunk_id",
        "project_id",
        "variant_id",
        "document_id",
        "chunk_to_retrieve",
        "doc_uri",
    }
    if missing := sorted(required - set(names)):
        raise JobContractError("AI Search response is missing columns: " + ", ".join(missing))
    rows: list[dict[str, Any]] = []
    for values in arrays:
        if not isinstance(values, list):
            raise JobContractError("AI Search result row is not an array")
        row_names = list(names)
        if len(values) == len(row_names) + 1 and "score" not in row_names:
            row_names.append("score")
        if len(values) != len(row_names):
            raise JobContractError("AI Search result row does not match its manifest")
        row = dict(zip(row_names, values, strict=True))
        for field in ("page_numbers", "parent_page_numbers"):
            if isinstance(row.get(field), str):
                try:
                    row[field] = json.loads(row[field])
                except json.JSONDecodeError:
                    row[field] = []
        rows.append(row)
    debug = value.get("debug_info") or {}
    return rows, debug if isinstance(debug, dict) else {}


def extract_reranker_ms(debug_info: Mapping[str, Any]) -> float | None:
    """Best-effort extraction across AI Search debug schema versions."""

    candidates: list[float] = []

    def visit(value: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                visit(item, (*path, str(key).lower()))
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            joined = ".".join(path)
            if "rerank" in joined and any(
                token in joined for token in ("ms", "time", "latency", "duration")
            ):
                candidates.append(float(value))

    visit(deep_dict(debug_info))
    return candidates[0] if candidates else None


def usage_counts(usage: Mapping[str, Any] | None) -> tuple[int | None, int | None, int | None]:
    data = deep_dict(usage or {})

    def integer(*keys: str) -> int | None:
        for key in keys:
            value = data.get(key)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    pass
        return None

    input_tokens = integer("input_tokens", "prompt_tokens")
    output_tokens = integer("output_tokens", "completion_tokens")
    total_tokens = integer("total_tokens")
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    return input_tokens, output_tokens, total_tokens


def trigger_index_sync_and_wait(
    workspace: Any,
    *,
    index_profile_key: str = BASELINE_INDEX_PROFILE_KEY,
    index_name: str = BASELINE_INDEX_NAME,
    source_table: str = BASELINE_CHUNK_TABLE,
    expected_rows: int = 40,
    trigger_sync: bool = True,
    timeout_seconds: int = 1800,
    poll_seconds: int = 10,
) -> dict[str, Any]:
    """Synchronize one pre-created Delta Sync Index and wait until it is online."""

    validate_variant_resource_pair(source_table, index_name)
    profiles = load_index_profiles()
    profile = profiles.get(str(index_profile_key))
    if profile is None:
        raise JobContractError("index_profile_key is not allow-listed")
    if (
        profile["source_table"] != source_table
        or profile["index_name"] != index_name
    ):
        raise JobContractError("Index profile does not match the requested resource pair")
    if (
        isinstance(expected_rows, bool)
        or not isinstance(expected_rows, int)
        or expected_rows < 0
    ):
        raise JobContractError("expected index row count must be a non-negative integer")
    service = workspace.vector_search_indexes

    def get_index() -> dict[str, Any]:
        try:
            payload = deep_dict(service.get_index(index_name=index_name))
        except (AttributeError, TypeError):
            encoded = quote(index_name, safe="")
            payload = deep_dict(
                workspace.api_client.do(
                    "GET", f"/api/2.0/vector-search/indexes/{encoded}"
                )
            )
        return validate_existing_index(payload, profile=profile)

    # This preflight happens before the sync request. A missing or mismatched
    # Index is never repaired, recreated, or granted permissions by this Job.
    get_index()
    if trigger_sync:
        try:
            service.sync_index(index_name=index_name)
        except (AttributeError, TypeError):
            encoded = quote(index_name, safe="")
            workspace.api_client.do(
                "POST", f"/api/2.0/vector-search/indexes/{encoded}/sync", body={}
            )

    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = get_index()
        status = last.get("status") if isinstance(last.get("status"), dict) else {}
        ready_value = status.get("ready")
        ready = ready_value is True or str(ready_value).lower() == "true"
        state = str(status.get("detailed_state") or status.get("state") or "").upper()
        message = str(status.get("message") or "")
        row_count = status.get("indexed_row_count")
        try:
            indexed_rows = int(row_count) if row_count is not None else None
        except (TypeError, ValueError):
            indexed_rows = None
        if ready and indexed_rows is not None and indexed_rows >= expected_rows and (
            not state or state in {"ONLINE", "ONLINE_NO_PENDING_UPDATE"}
        ):
            return last
        if any(token in state or token in message.upper() for token in ("FAIL", "ERROR", "OFFLINE")):
            raise RuntimeError(f"AI Search sync failed: {state} {message}".strip())
        time.sleep(max(1, poll_seconds))
    raise TimeoutError("AI Search index did not become ready before the timeout")
