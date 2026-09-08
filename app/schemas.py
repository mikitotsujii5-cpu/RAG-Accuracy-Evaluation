"""Strict public request/response contracts for the web application."""

from __future__ import annotations

import json
import re
from datetime import date
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, UUID4, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProjectCreate(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    description: Annotated[str, Field(max_length=1000)] = ""


DOCUMENT_METADATA_RESERVED_KEYS = frozenset({
    "title", "summary", "category", "tags", "document_date", "source",
    "document_id", "project_id", "doc_uri",
})
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class DocumentMetadata(StrictModel):
    """Generic document metadata with optional Toyota compatibility fields.

    A PDF is the only required upload input.  The first group is the generic
    contract used by the UI.  The four legacy fields remain accepted so older
    clients and the seeded Toyota evaluation corpus continue to work.
    """

    title: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    summary: Annotated[str, Field(min_length=1, max_length=300)] | None = None
    category: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    tags: Annotated[list[Annotated[str, Field(min_length=1, max_length=80)]], Field(max_length=20)] = Field(
        default_factory=list
    )
    document_date: date | None = None
    source: Annotated[str, Field(min_length=1, max_length=500)] | None = None
    custom_metadata: Annotated[dict[str, str], Field(max_length=20)] = Field(
        default_factory=dict
    )

    # Backward-compatible Toyota demo fields.  New generic clients do not need
    # to send these values.
    model: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    model_year: Annotated[int, Field(ge=1900, le=2100)] | None = None
    document_type: Annotated[str, Field(min_length=1, max_length=80)] | None = None
    vehicle_category: Annotated[str, Field(min_length=1, max_length=80)] | None = None

    @field_validator(
        "title", "summary", "category", "source", "model", "document_type", "vehicle_category",
        mode="before",
    )
    @classmethod
    def empty_optional_text_is_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("document_date", mode="before")
    @classmethod
    def require_iso_document_date(cls, value: Any) -> Any:
        if value is None or value == "":
            return None
        if isinstance(value, str) and not _ISO_DATE.fullmatch(value.strip()):
            raise ValueError("document_dateはYYYY-MM-DD形式で指定してください。")
        return value

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, value: Any) -> Any:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("tagsは文字列の配列で指定してください。")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise ValueError("tagsは文字列の配列で指定してください。")
            tag = item.strip()
            if not tag or _CONTROL_CHARACTER.search(tag):
                raise ValueError("tagに空文字または制御文字は使用できません。")
            folded = tag.casefold()
            if folded in seen:
                raise ValueError("tagsに大文字・小文字違いを含む重複があります。")
            seen.add(folded)
            normalized.append(tag)
        return normalized

    @field_validator("custom_metadata", mode="before")
    @classmethod
    def normalize_custom_metadata(cls, value: Any) -> Any:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("custom_metadataはキーと値の組で指定してください。")
        if len(value) > 20:
            raise ValueError("custom_metadataは20件以下にしてください。")
        normalized: dict[str, str] = {}
        seen: set[str] = set()
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str) or not isinstance(raw_value, str):
                raise ValueError("custom_metadataのキーと値は文字列にしてください。")
            key = raw_key.strip()
            item = raw_value.strip()
            if not key or not item:
                raise ValueError("custom_metadataのキーと値は空にできません。")
            if len(key) > 80 or len(item) > 1000:
                raise ValueError("custom_metadataのキーは80文字、値は1000文字以下にしてください。")
            if _CONTROL_CHARACTER.search(key) or _CONTROL_CHARACTER.search(item):
                raise ValueError("custom_metadataに制御文字は使用できません。")
            folded = key.casefold()
            if folded in DOCUMENT_METADATA_RESERVED_KEYS:
                raise ValueError(f"custom_metadataのキー「{key}」は予約済みです。")
            if folded in seen:
                raise ValueError("custom_metadataに大文字・小文字違いを含む重複キーがあります。")
            seen.add(folded)
            normalized[key] = item
        return normalized


def parse_document_metadata_json(value: str) -> DocumentMetadata:
    """Parse upload metadata without silently accepting duplicate JSON keys."""

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        seen: set[str] = set()
        for key, item in pairs:
            folded = key.casefold()
            if folded in seen:
                raise ValueError(f"JSONに重複キーがあります: {key}")
            seen.add(folded)
            result[key] = item
        return result

    payload = json.loads(value, object_pairs_hook=unique_object)
    return DocumentMetadata.model_validate(payload)


class ChunkMethod(str, Enum):
    standard = "STANDARD"
    semantic = "SEMANTIC"
    parent_child = "PARENT_CHILD"


class ContentProfile(str, Enum):
    text_only = "TEXT_ONLY"
    layout_preserving = "LAYOUT_PRESERVING"


class PreparationConfiguration(StrictModel):
    chunk_method: ChunkMethod
    chunk_size_tokens: Literal[256, 512, 1024]
    parent_chunk_size_tokens: int | None = Field(default=None, ge=512, le=8192)
    content_profile: ContentProfile = ContentProfile.layout_preserving
    cleaning_enabled: bool = True
    semantic_metadata_enabled: bool = True
    embedding_model_key: Annotated[str, Field(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def validate_parent_size(self) -> "PreparationConfiguration":
        if self.chunk_method is ChunkMethod.parent_child:
            if self.parent_chunk_size_tokens is None:
                self.parent_chunk_size_tokens = min(self.chunk_size_tokens * 4, 4096)
            if self.parent_chunk_size_tokens <= self.chunk_size_tokens:
                raise ValueError("Parent chunkはChild chunkより大きくしてください。")
        elif self.parent_chunk_size_tokens is not None:
            raise ValueError("Parent chunkサイズはParent-childでだけ指定できます。")
        return self


class PreparationRequest(StrictModel):
    run_type: Literal["BUILD_VARIANT"] = "BUILD_VARIANT"
    document_ids: Annotated[list[str], Field(min_length=1, max_length=100)]
    index_profile_key: Annotated[
        str,
        Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"),
    ]
    configuration: PreparationConfiguration

    @field_validator("document_ids")
    @classmethod
    def unique_document_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("document_idsに重複があります。")
        return value


PHASE_PRESETS: dict[str, dict[str, Any]] = {
    "phase_01": {
        "label": "Phase 1",
        "query_type": "ANN",
        "metadata_filtering": False,
        "reranking": False,
        "query_optimization": False,
    },
    "phase_02": {
        "label": "Phase 2",
        "query_type": "HYBRID",
        "metadata_filtering": False,
        "reranking": False,
        "query_optimization": False,
    },
    "phase_03": {
        "label": "Phase 3",
        "query_type": "HYBRID",
        "metadata_filtering": True,
        "reranking": False,
        "query_optimization": False,
    },
    "phase_04": {
        "label": "Phase 4",
        "query_type": "HYBRID",
        "metadata_filtering": True,
        "reranking": True,
        "query_optimization": False,
    },
    "phase_05": {
        "label": "Phase 5",
        "query_type": "HYBRID",
        "metadata_filtering": True,
        "reranking": True,
        "query_optimization": True,
    },
}


class PresetRetrieval(StrictModel):
    mode: Literal["PRESET"]
    phase_id: Literal["phase_01", "phase_02", "phase_03", "phase_04", "phase_05"]


class CustomRetrieval(StrictModel):
    mode: Literal["CUSTOM"]
    query_type: Literal["ANN", "HYBRID"]
    metadata_filtering: bool
    reranking: bool
    query_optimization: bool


class ChatRequest(StrictModel):
    message: Annotated[str, Field(min_length=1, max_length=8000)]
    rag_mode: Literal["DETERMINISTIC", "AGENTIC"] = "DETERMINISTIC"
    retrieval: PresetRetrieval | CustomRetrieval = Field(discriminator="mode")
    variant_id: Annotated[str, Field(min_length=1, max_length=128)]
    answer_model_key: Annotated[str, Field(min_length=1, max_length=128)]
    # Generated once by the browser before the request starts.  It lets the UI
    # correlate stop requests with a turn immediately and prevents an HTTP
    # retry from silently receiving a different run identifier.
    client_request_id: UUID4 | None = None


class SessionCreate(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=120)] = "新しい会話"


class EvaluationCaseCreate(StrictModel):
    """One project-scoped, domain-neutral RAG evaluation question."""

    question: Annotated[str, Field(min_length=1, max_length=8000)]
    expected_answer: Annotated[str, Field(min_length=1, max_length=8000)] | None = None
    expected_facts: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=1000)]],
        Field(max_length=20),
    ] = Field(default_factory=list)
    relevant_document_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    relevant_pages: Annotated[
        list[Annotated[int, Field(ge=1, le=100000)]],
        Field(max_length=100),
    ] = Field(default_factory=list)
    question_type: Annotated[str, Field(min_length=1, max_length=80)] = "general"
    is_answerable: bool = True
    language: Annotated[str, Field(min_length=2, max_length=20)] = "ja"
    dataset_version: Annotated[
        str,
        Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
    ] = "v1.0.0"
    dataset_split: Literal["development", "holdout"] = "development"

    @field_validator("expected_answer", "relevant_document_id", mode="before")
    @classmethod
    def empty_case_text_is_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("expected_facts")
    @classmethod
    def unique_expected_facts(cls, value: list[str]) -> list[str]:
        if len(value) != len({item.casefold() for item in value}):
            raise ValueError("expected_factsに重複があります。")
        return value

    @field_validator("relevant_pages")
    @classmethod
    def unique_relevant_pages(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("relevant_pagesに重複があります。")
        return sorted(value)

    @model_validator(mode="after")
    def validate_answerability_evidence(self) -> "EvaluationCaseCreate":
        if self.is_answerable and (
            self.relevant_document_id is None or not self.relevant_pages
        ):
            raise ValueError("回答可能な質問には正解PDFとページを指定してください。")
        if not self.is_answerable and (self.relevant_document_id or self.relevant_pages):
            raise ValueError("回答不能な質問には正解PDF・ページを指定しないでください。")
        return self


class EvaluationRequest(StrictModel):
    phase_ids: Annotated[list[Literal[
        "phase_01", "phase_02", "phase_03", "phase_04", "phase_05"
    ]], Field(min_length=1, max_length=5)]
    trial_count: Annotated[int, Field(ge=1, le=10)] = 3
    dataset_version: Annotated[
        str,
        Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
    ] = "v1.0.0"
    dataset_split: Literal["development", "holdout"] = "development"
    # ``None`` keeps the existing whole-dataset behaviour.  When the UI sends
    # IDs, the repository verifies that every case belongs to the selected
    # Project/version/split before a Lakeflow Job is submitted.
    evaluation_case_ids: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=128)]],
        Field(min_length=1, max_length=1000),
    ] | None = None
    variant_id: Annotated[str, Field(min_length=1, max_length=128)]
    answer_model_key: Annotated[str, Field(min_length=1, max_length=128)]
    judge_model_key: Annotated[str, Field(min_length=1, max_length=128)]
    final_k: Literal[10] = 10

    @field_validator("phase_ids")
    @classmethod
    def unique_phases(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("phase_idsに重複があります。")
        return value

    @field_validator("evaluation_case_ids")
    @classmethod
    def unique_evaluation_cases(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("evaluation_case_idsに空のIDは指定できません。")
        if len(normalized) != len(set(normalized)):
            raise ValueError("evaluation_case_idsに重複があります。")
        return normalized


class ImprovementSuggestion(StrictModel):
    phase_id: Literal["phase_01", "phase_02", "phase_03", "phase_04", "phase_05"]
    evidence_case_ids: Annotated[list[str], Field(min_length=1, max_length=20)]
    diagnosis: Annotated[str, Field(min_length=1, max_length=2000)]
    priority: Literal["high", "medium", "low"]
    target: Literal[
        "retrieval", "metadata", "reranking", "query_optimization",
        "chunking", "embedding", "prompt", "evaluation_data",
    ]
    proposed_change: Annotated[str, Field(min_length=1, max_length=2000)]
    expected_effect: Annotated[str, Field(min_length=1, max_length=1000)]
    tradeoffs: Annotated[list[str], Field(max_length=10)]
    retest_plan: Annotated[str, Field(min_length=1, max_length=1000)]


def resolve_retrieval_config(retrieval: PresetRetrieval | CustomRetrieval) -> dict[str, Any]:
    if isinstance(retrieval, PresetRetrieval):
        return {"phase_id": retrieval.phase_id, **PHASE_PRESETS[retrieval.phase_id]}
    return {
        "phase_id": "custom",
        "label": "Custom",
        "query_type": retrieval.query_type,
        "metadata_filtering": retrieval.metadata_filtering,
        "reranking": retrieval.reranking,
        "query_optimization": retrieval.query_optimization,
    }
