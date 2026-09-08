from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path


JOBS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(JOBS_DIR))

import job_common as common  # noqa: E402


def test_generic_metadata_is_added_to_embedding_context_without_vehicle_fields() -> None:
    chunks = common.build_document_chunks(
        {
            "document_id": str(uuid.uuid4()),
            "title": "情報セキュリティ規程",
            "summary": "全社員向けの規程",
            "category": "社内規程",
            "tags": ["情報セキュリティ", "全社員"],
            "document_date": "2026-04-01",
            "source": "情報システム部",
            "metadata_json": json.dumps(
                {"schema_version": "1.0", "custom": {"版": "第3版"}},
                ensure_ascii=False,
            ),
            "model": None,
            "model_year": None,
            "document_type": None,
            "vehicle_category": None,
        },
        [{
            "element_id": 1,
            "page_number": 1,
            "element_type": "paragraph",
            "content": "パスワードは会社の規程に従って管理します。",
        }],
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

    embedding_text = chunks[0]["chunk_to_embed"]
    assert "カテゴリ: 社内規程" in embedding_text
    assert "タグ: 情報セキュリティ, 全社員" in embedding_text
    assert "文書日付: 2026-04-01" in embedding_text
    assert "出典: 情報システム部" in embedding_text
    assert "メタデータ: 版: 第3版" in embedding_text
    assert "車種:" not in embedding_text


def test_precreated_source_schema_carries_generic_metadata_columns() -> None:
    source = (JOBS_DIR / "data_preparation_job.py").read_text(encoding="utf-8")
    for column in ("category", "tags", "document_date", "source", "metadata_json"):
        assert f'T.StructField("{column}"' in source
        assert f'"{column}"' in source
    assert 'option("overwriteSchema", "true")' not in source
