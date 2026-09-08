from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest


JOBS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(JOBS_DIR))

import job_common as common  # noqa: E402


SECURITY_ID = "11111111-1111-4111-8111-111111111111"
PRODUCT_ID = "22222222-2222-4222-8222-222222222222"

GENERIC_ROWS = [
    {
        "document_id": SECURITY_ID,
        "category": "社内規程",
        "tags": ["情報セキュリティ", "全社員"],
        "document_date": date(2026, 4, 1),
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
    {
        "document_id": PRODUCT_ID,
        "category": "製品マニュアル",
        "tags": '["データ基盤", "管理者"]',
        "document_date": "2025-10-01",
        "source": "製品開発部",
        "metadata_json": json.dumps(
            {
                "schema_version": "1.0",
                "custom": {"版": "第1版", "機密区分": "公開"},
            },
            ensure_ascii=False,
        ),
        "model": None,
        "model_year": None,
        "document_type": None,
        "vehicle_category": None,
    },
]

VEHICLE_MASTER = [
    {
        "model": "Prius",
        "aliases": ["プリウス", "PRIUS"],
        "valid_model_years": [2023, 2024],
        "vehicle_category": "passenger_car",
    },
    {
        "model": "Crown Sport",
        "aliases": ["クラウンスポーツ", "CROWN SPORT"],
        "valid_model_years": [2024],
        "vehicle_category": "suv",
    },
]


@pytest.mark.parametrize(
    "question",
    [
        "社内規程を確認したい",
        "情報セキュリティの資料を確認したい",
        "2026年4月1日の文書を確認したい",
        "情報システム部の文書を確認したい",
        "機密区分が社内公開の文書を確認したい",
    ],
)
def test_generic_registry_metadata_becomes_document_id_filter(question: str) -> None:
    search_filter, semantic_filter = common.resolve_project_search_filters(
        question,
        GENERIC_ROWS,
        VEHICLE_MASTER,
    )

    assert search_filter == {"document_id": [SECURITY_ID]}
    assert semantic_filter == {}


def test_custom_value_requires_both_key_and_value() -> None:
    search_filter, semantic_filter = common.resolve_project_search_filters(
        "社内公開の資料を確認したい",
        GENERIC_ROWS,
        VEHICLE_MASTER,
    )

    assert search_filter == {}
    assert semantic_filter == {}


def test_conflicting_generic_dimensions_fall_back_to_unfiltered_search() -> None:
    search_filter, _ = common.resolve_project_search_filters(
        "情報システム部の製品マニュアルを確認したい",
        GENERIC_ROWS,
        VEHICLE_MASTER,
    )

    assert search_filter == {}


def test_json_envelope_common_fields_are_used_as_typed_column_fallback() -> None:
    rows = [
        {
            "document_id": SECURITY_ID,
            "category": None,
            "tags": None,
            "source": None,
            "metadata_json": json.dumps(
                {
                    "common": {
                        "category": "監査報告",
                        "tags": ["重要"],
                        "source": "監査室",
                    },
                    "custom": {},
                },
                ensure_ascii=False,
            ),
        },
        {"document_id": PRODUCT_ID, "category": "操作手順", "tags": []},
    ]

    assert common.resolve_project_filter_document_ids("監査室の報告", rows) == [
        SECURITY_ID
    ]


def test_legacy_filters_keep_semantic_labels_but_search_by_document_id() -> None:
    prius_owner = "33333333-3333-4333-8333-333333333333"
    prius_equipment = "44444444-4444-4444-8444-444444444444"
    crown_equipment = "55555555-5555-4555-8555-555555555555"
    rows = [
        {
            "document_id": prius_owner,
            "model": "Prius",
            "model_year": 2024,
            "document_type": "owners_guide",
            "vehicle_category": "passenger_car",
        },
        {
            "document_id": prius_equipment,
            "model": "Prius",
            "model_year": 2024,
            "document_type": "equipment_spec",
            "vehicle_category": "passenger_car",
        },
        {
            "document_id": crown_equipment,
            "model": "Crown Sport",
            "model_year": 2024,
            "document_type": "equipment_spec",
            "vehicle_category": "suv",
        },
    ]

    search_filter, semantic_filter = common.resolve_project_search_filters(
        "2024年式プリウスの標準装備を確認したい",
        rows,
        VEHICLE_MASTER,
    )

    assert semantic_filter == {
        "model": "Prius",
        "model_year": 2024,
        "document_type": "equipment_spec",
    }
    assert search_filter == {"document_id": [prius_equipment]}
    assert common.filter_exact_match(semantic_filter, semantic_filter) is True


def test_vehicle_alias_stored_by_legacy_client_resolves_to_canonical_model() -> None:
    alias_document = "66666666-6666-4666-8666-666666666666"
    another_document = "77777777-7777-4777-8777-777777777777"
    rows = [
        {
            "document_id": alias_document,
            "model": "プリウス",
            "model_year": 2024,
            "document_type": "owners_guide",
            "vehicle_category": "passenger_car",
        },
        {
            "document_id": another_document,
            "model": "Crown Sport",
            "model_year": 2024,
            "document_type": "owners_guide",
            "vehicle_category": "suv",
        },
    ]

    search_filter, semantic_filter = common.resolve_project_search_filters(
        "2024年式プリウスについて確認したい",
        rows,
        VEHICLE_MASTER,
    )

    assert semantic_filter == {"model": "Prius", "model_year": 2024}
    assert search_filter == {"document_id": [alias_document]}


def test_non_vehicle_project_does_not_apply_crown_vehicle_filter() -> None:
    search_filter, semantic_filter = common.resolve_project_search_filters(
        "Crown Sportという名称を含む契約条項を確認したい",
        GENERIC_ROWS,
        VEHICLE_MASTER,
    )

    assert search_filter == {}
    assert semantic_filter == {}


def test_legacy_looking_custom_key_remains_generic_metadata() -> None:
    rows = [
        {
            **GENERIC_ROWS[0],
            "metadata_json": json.dumps(
                {"schema_version": "1.0", "custom": {"model": "Crown"}},
                ensure_ascii=False,
            ),
        },
        GENERIC_ROWS[1],
    ]

    search_filter, semantic_filter = common.resolve_project_search_filters(
        "model Crown の資料を確認したい",
        rows,
        VEHICLE_MASTER,
    )

    assert search_filter == {"document_id": [SECURITY_ID]}
    assert semantic_filter == {}


def test_missing_expected_filter_is_null_not_a_false_success() -> None:
    assert common.filter_exact_match({}, None) is None
    assert common.filter_exact_match({}, {"model": None, "model_year": None}) is None
    assert common.filter_exact_match(
        {"model": "Prius"},
        {"model": "Prius", "model_year": None},
    ) is True


def test_evaluation_job_uses_generic_prompt_and_separate_filter_values() -> None:
    source = (JOBS_DIR / "evaluation_job.py").read_text(encoding="utf-8")

    assert "任意分野のPDF資料に対応するRAG回答アシスタント" in source
    assert "自動車資料のRAG回答アシスタント" not in source
    assert "固有名詞、型番、専門用語、日付" in source
    assert "resolve_project_search_filters(" in source
    assert '"semantic_filters": semantic_filters' in source
    assert "filter_exact_match(\n                                    semantic_filters," in source
