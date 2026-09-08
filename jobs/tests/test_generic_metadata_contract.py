from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
JOBS_DIR = REPO / "jobs"
sys.path.insert(0, str(JOBS_DIR))

import job_common as common  # noqa: E402


def test_non_vehicle_metadata_is_added_to_embedding_context() -> None:
    chunks = common.build_document_chunks(
        {
            "document_id": str(uuid.uuid4()),
            "title": "情報セキュリティ規程",
            "summary": "全社員が守る情報管理ルールです。",
            "category": "社内規程",
            "tags": ["情報セキュリティ", "全社員"],
            "document_date": "2026-04-01",
            "source": "情報システム部",
            "metadata_json": json.dumps(
                {
                    "schema_version": "1.0",
                    "custom": {"版": "第3版", "機密区分": "社内公開"},
                },
                ensure_ascii=False,
            ),
            "model": None,
            "model_year": None,
            "document_type": None,
            "vehicle_category": None,
        },
        [
            {
                "element_id": 1,
                "page_number": 1,
                "element_type": "paragraph",
                "content": "パスワードを第三者と共有してはいけません。",
            }
        ],
        {
            "chunk_method": "STANDARD",
            "chunk_size_tokens": 256,
            "parent_chunk_size_tokens": None,
            "content_profile": "LAYOUT_PRESERVING",
            "cleaning_enabled": True,
            "semantic_metadata_enabled": True,
            "embedding_model_key": common.BASELINE_EMBEDDING_MODEL_KEY,
        },
        project_id=str(uuid.uuid4()),
        variant_id=str(uuid.uuid4()),
    )

    assert chunks
    embedded = chunks[0]["chunk_to_embed"]
    for expected in (
        "情報セキュリティ規程",
        "社内規程",
        "情報セキュリティ",
        "全社員",
        "2026-04-01",
        "情報システム部",
        "版: 第3版",
        "機密区分: 社内公開",
    ):
        assert expected in embedded
    assert "車種:" not in embedded


def test_semantic_metadata_off_does_not_leak_document_metadata_into_embedding() -> None:
    chunks = common.build_document_chunks(
        {
            "document_id": str(uuid.uuid4()),
            "title": "情報セキュリティ規程",
            "category": "社内規程",
            "tags": ["機密"],
            "metadata_json": '{"custom":{"版":"第3版"}}',
        },
        [
            {
                "element_id": 1,
                "page_number": 1,
                "element_type": "paragraph",
                "content": "本文だけをEmbeddingへ渡します。",
            }
        ],
        {
            "chunk_method": "STANDARD",
            "chunk_size_tokens": 256,
            "parent_chunk_size_tokens": None,
            "content_profile": "LAYOUT_PRESERVING",
            "cleaning_enabled": True,
            "semantic_metadata_enabled": False,
            "embedding_model_key": common.BASELINE_EMBEDDING_MODEL_KEY,
        },
        project_id=str(uuid.uuid4()),
        variant_id=str(uuid.uuid4()),
    )

    assert chunks[0]["chunk_to_embed"] == chunks[0]["chunk_to_retrieve"]


def test_precreated_source_schema_carries_generic_and_legacy_metadata() -> None:
    source = (JOBS_DIR / "data_preparation_job.py").read_text(encoding="utf-8")
    for field in (
        "category",
        "tags",
        "document_date",
        "source",
        "metadata_json",
        "model",
        "model_year",
        "document_type",
        "vehicle_category",
    ):
        assert f'T.StructField("{field}"' in source
    # The administrator creates the physical Delta Table and AI Search Index.
    # Runtime code must validate that schema, not evolve it as a side effect.
    assert 'option("overwriteSchema", "true")' not in source


def test_delta_migration_is_schema_evolving_repeatable_and_backfills_legacy_rows() -> None:
    migration = (REPO / "sql" / "07_migrate_generic_document_metadata.sql").read_text(
        encoding="utf-8"
    )
    assert "MERGE WITH SCHEMA EVOLUTION" in migration
    for field in ("category", "tags", "document_date", "source", "metadata_json"):
        assert field in migration
    assert "'schema_version', '1.0'" in migration
    assert "'legacy', named_struct(" in migration
    assert "bad_schema_version" in migration


def test_all_five_phase_presets_remain_unchanged_for_legacy_evaluation() -> None:
    assert list(common.PHASE_PRESETS) == [
        "phase_01",
        "phase_02",
        "phase_03",
        "phase_04",
        "phase_05",
    ]
    assert common.PHASE_PRESETS["phase_01"] == {
        "query_type": "ANN",
        "metadata_filtering": False,
        "reranking": False,
        "query_optimization": False,
    }
    assert common.PHASE_PRESETS["phase_05"] == {
        "query_type": "HYBRID",
        "metadata_filtering": True,
        "reranking": True,
        "query_optimization": True,
    }
