from __future__ import annotations

import hashlib
import json
import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


JOBS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(JOBS_DIR))

import job_common as common  # noqa: E402


class JobCommonTest(unittest.TestCase):
    def test_sql_string_array_escapes_each_value_and_supports_empty_arrays(self) -> None:
        self.assertEqual(
            common.sql_string_array(["alpha", "O'Brien"]),
            "array('alpha','O''Brien')",
        )
        self.assertEqual(common.sql_string_array([]), "array()")

    @staticmethod
    def _embedding_model(
        model_key: str,
        display_name: str,
        *,
        ready: bool = True,
    ) -> dict:
        return {
            "model_key": model_key,
            "display_name": display_name,
            "target_kind": "FMAPI_ENDPOINT",
            "endpoint_state": "READY" if ready else "UNAVAILABLE",
            "selectable": ready,
            "region_available": ready,
            "capabilities": ["embedding"],
        }

    def test_qwen_embedding_is_preferred_when_ready(self) -> None:
        rows = [
            self._embedding_model("emb-a", "A English"),
            self._embedding_model(
                common.BASELINE_EMBEDDING_MODEL_KEY,
                "Qwen3 Embedding 0.6B",
            ),
        ]
        self.assertEqual(
            common.select_default_model_key(
                rows,
                capability="embedding",
                preferred_model_key=common.BASELINE_EMBEDDING_MODEL_KEY,
            ),
            common.BASELINE_EMBEDDING_MODEL_KEY,
        )

    def test_embedding_default_fallback_is_deterministic_when_qwen_unavailable(self) -> None:
        rows = [
            self._embedding_model("emb-gte", "GTE Large (En)"),
            self._embedding_model(
                common.BASELINE_EMBEDDING_MODEL_KEY,
                "Qwen3 Embedding 0.6B",
                ready=False,
            ),
            self._embedding_model("emb-bge", "BGE Large (En)"),
        ]
        for reordered in (rows, list(reversed(rows))):
            self.assertEqual(
                common.select_default_model_key(
                    reordered,
                    capability="embedding",
                    preferred_model_key=common.BASELINE_EMBEDDING_MODEL_KEY,
                ),
                "emb-bge",
            )

    def test_starter_questions_are_project_scoped_unlabelled_and_idempotent(self) -> None:
        project_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        document = {
            "project_id": project_id,
            "document_id": document_id,
            "doc_uri": f"/Volumes/catalog/schema/volume/projects/{project_id}/manual.pdf",
            "title": "情報セキュリティ規程",
        }
        generated = common.build_starter_evaluation_cases(
            project_id=project_id,
            registry_rows=[document],
            existing_cases=[],
            created_at="2026-09-07T00:00:00Z",
        )
        self.assertEqual(
            [item["eval_case_id"] for item in generated],
            list(common.STARTER_EVAL_CASE_IDS),
        )
        self.assertEqual(len({item["question"] for item in generated}), 3)
        self.assertTrue(all(item["project_id"] == project_id for item in generated))
        self.assertTrue(all(item["expected_answer"] is None for item in generated))
        self.assertTrue(all(item["relevant_doc_uri"] == document["doc_uri"] for item in generated))

        existing = [
            {
                "eval_case_id": item["eval_case_id"],
                "question": item["question"],
                "question_type": item["question_type"],
                "dataset_version": item["dataset_version"],
                "dataset_split": item["dataset_split"],
            }
            for item in generated
        ]
        self.assertEqual(
            common.build_starter_evaluation_cases(
                project_id=project_id,
                registry_rows=[document],
                existing_cases=existing,
                created_at="later",
            ),
            [],
        )

    def test_starter_question_validation_forbids_fabricated_labels(self) -> None:
        project_id = str(uuid.uuid4())
        case = {
            "project_id": project_id,
            "eval_case_id": common.STARTER_EVAL_CASE_IDS[0],
            "question": "概要は？",
            "expected_answer": "未検証の答え",
            "expected_facts": [],
            "relevant_doc_uri": "/Volumes/example.pdf",
            "relevant_pages": [],
            "relevance_judgments": [],
            "question_type": common.STARTER_QUESTION_TYPE,
            "dataset_version": common.STARTER_DATASET_VERSION,
            "dataset_split": common.STARTER_DATASET_SPLIT,
        }
        with self.assertRaisesRegex(common.JobContractError, "starter sample"):
            common.validate_eval_cases(
                [case],
                project_id=project_id,
                dataset_version=common.STARTER_DATASET_VERSION,
                dataset_split=common.STARTER_DATASET_SPLIT,
            )
        self.assertFalse(common.evaluation_case_has_answer_reference({}))
        self.assertTrue(
            common.evaluation_case_has_answer_reference(
                {"expected_facts": ["人が検証した事実"]}
            )
        )

    def test_mlflow_experiment_contract(self) -> None:
        self.assertEqual(
            common.MLFLOW_EXPERIMENT_PATH,
            os.getenv("MLFLOW_EXPERIMENT_PATH", "").strip(),
        )
        self.assertEqual(
            common.MLFLOW_EXPERIMENT_ID,
            os.getenv("MLFLOW_EXPERIMENT_ID", "").strip(),
        )
        self.assertEqual(
            common.MLFLOW_TRACING_SQL_WAREHOUSE_ID,
            os.getenv("MLFLOW_TRACING_SQL_WAREHOUSE_ID", "").strip(),
        )
    @staticmethod
    def _index_profile(
        method: str = "STANDARD",
        size: int = 512,
        *,
        key: str = "manual-standard-512",
        embedding_model_key: str = common.BASELINE_EMBEDDING_MODEL_KEY,
    ) -> dict:
        suffix = f"{method.casefold()}_{size}"
        return {
            "key": key,
            "source_table": f"{common.UC_PREFIX}.manual_chunks_{suffix}",
            "index_name": f"{common.UC_PREFIX}.manual_chunks_{suffix}_index",
            "search_endpoint": common.BASELINE_SEARCH_ENDPOINT,
            "chunk_method": method,
            "chunk_size_tokens": size,
            "parent_chunk_size_tokens": (
                min(size * 4, 4096) if method == "PARENT_CHILD" else None
            ),
            "content_profile": "LAYOUT_PRESERVING",
            "cleaning_enabled": True,
            "semantic_metadata_enabled": True,
            "embedding_model_key": embedding_model_key,
            "embedding_endpoint": common.BASELINE_EMBEDDING_ENDPOINT,
        }

    def _prep_record(
        self,
        method: str,
        size: int,
        *,
        profile_key: str = common.BASELINE_INDEX_PROFILE_KEY,
    ) -> tuple[dict, dict]:
        configuration = {
            "run_type": "BUILD_VARIANT",
            "document_ids": sorted(common.BASELINE_DOCUMENT_IDS),
            "index_profile_key": profile_key,
            "configuration": {
                "chunk_method": method,
                "chunk_size_tokens": size,
                "parent_chunk_size_tokens": min(size * 4, 4096)
                if method == "PARENT_CHILD" else None,
                "content_profile": "LAYOUT_PRESERVING",
                "cleaning_enabled": True,
                "semantic_metadata_enabled": True,
                "embedding_model_key": common.BASELINE_EMBEDDING_MODEL_KEY,
            },
        }
        record = {
            "prep_run_id": str(uuid.uuid4()),
            "project_id": common.BASELINE_PROJECT_ID,
            "run_type": "BUILD_VARIANT",
            "document_ids": sorted(common.BASELINE_DOCUMENT_IDS),
            "target_variant_id": str(uuid.uuid4()),
            "normalized_config_json": common.canonical_json(configuration),
            "config_hash": common.canonical_json_hash(configuration),
        }
        return configuration, record

    def test_canonical_hash_matches_app_contract(self) -> None:
        value = {"日本語": "値", "b": [2, 1], "a": True}
        expected_text = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        expected = hashlib.sha256(expected_text.encode("utf-8")).hexdigest()
        self.assertEqual(common.canonical_json_hash(value), expected)

    def test_validate_baseline_prep_record(self) -> None:
        configuration, record = self._prep_record("STANDARD", 512)
        self.assertEqual(common.validate_baseline_prep_record(record), configuration)
        record["document_ids"] = record["document_ids"][:-1]
        with self.assertRaises(common.JobContractError):
            common.validate_baseline_prep_record(record)

    def test_validate_preparation_record_allows_non_baseline_project(self) -> None:
        project_id = str(uuid.uuid4())
        document_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        configuration, record = self._prep_record("STANDARD", 512)
        configuration["document_ids"] = document_ids
        record.update(
            project_id=project_id,
            document_ids=document_ids,
            normalized_config_json=common.canonical_json(configuration),
            config_hash=common.canonical_json_hash(configuration),
        )
        self.assertEqual(common.validate_preparation_record(record), configuration)

    def test_validate_preparation_record_verifies_server_activation_flag(self) -> None:
        configuration, record = self._prep_record("STANDARD", 512)
        configuration["activate_on_success"] = False
        record["normalized_config_json"] = common.canonical_json(configuration)
        record["config_hash"] = common.canonical_json_hash(configuration)
        self.assertFalse(
            common.validate_preparation_record(record)["activate_on_success"]
        )

        configuration["activate_on_success"] = "false"
        record["normalized_config_json"] = common.canonical_json(configuration)
        record["config_hash"] = common.canonical_json_hash(configuration)
        with self.assertRaisesRegex(
            common.JobContractError, "activate_on_success must be boolean"
        ):
            common.validate_preparation_record(record)

    def test_registry_scope_rejects_cross_project_documents(self) -> None:
        project_id = str(uuid.uuid4())
        other_project = str(uuid.uuid4())
        first, second = str(uuid.uuid4()), str(uuid.uuid4())
        rows = [
            {"project_id": project_id, "document_id": first},
            {"project_id": other_project, "document_id": second},
        ]
        with self.assertRaisesRegex(common.JobContractError, "another Project"):
            common.validate_registry_document_scope(
                project_id=project_id,
                requested_document_ids=[first, second],
                registry_rows=rows,
            )

    def test_all_nine_chunk_profiles_can_run_only_when_manually_registered(self) -> None:
        for method in ("STANDARD", "SEMANTIC", "PARENT_CHILD"):
            for size in (256, 512, 1024):
                with self.subTest(method=method, size=size):
                    key = f"manual-{method.casefold()}-{size}"
                    profile = self._index_profile(method, size, key=key)
                    configuration, record = self._prep_record(
                        method,
                        size,
                        profile_key=key,
                    )
                    with patch.dict(
                        os.environ,
                        {"RAG_INDEX_PROFILES_JSON": json.dumps([profile])},
                    ):
                        self.assertEqual(
                            common.validate_preparation_record(record),
                            configuration,
                        )

    def test_existing_profile_resolves_to_exact_fixed_table_and_index(self) -> None:
        profiles = common.load_index_profiles("")
        profile = profiles[common.BASELINE_INDEX_PROFILE_KEY]
        configuration = {
            field: profile[field]
            for field in common.INDEX_PROFILE_CONFIGURATION_FIELDS
        }

        resolved = common.resolve_index_profile(
            common.BASELINE_INDEX_PROFILE_KEY,
            configuration,
            profiles=profiles,
        )

        self.assertEqual(resolved["source_table"], common.BASELINE_CHUNK_TABLE)
        self.assertEqual(resolved["index_name"], common.BASELINE_INDEX_NAME)
        self.assertNotIn("{", resolved["source_table"])
        self.assertNotIn("{", resolved["index_name"])
        self.assertFalse(hasattr(common, "variant_resource_names"))

    def test_index_profile_allowlist_fails_closed(self) -> None:
        profile = self._index_profile()
        profiles = common.load_index_profiles(json.dumps([profile]))
        configuration = {
            field: profile[field]
            for field in common.INDEX_PROFILE_CONFIGURATION_FIELDS
        }
        with self.assertRaisesRegex(common.JobContractError, "not allow-listed"):
            common.resolve_index_profile("unregistered-profile", configuration, profiles=profiles)
        with self.assertRaisesRegex(common.JobContractError, "does not match"):
            common.resolve_index_profile(
                profile["key"],
                {**configuration, "chunk_size_tokens": 1024},
                profiles=profiles,
            )
        with self.assertRaisesRegex(common.JobContractError, "not allow-listed"):
            common.validate_variant_resource_pair(
                f"{common.UC_PREFIX}.not_registered",
                f"{common.UC_PREFIX}.not_registered_index",
            )

    def test_index_profile_json_rejects_ambiguous_or_unsafe_entries(self) -> None:
        profile = self._index_profile()
        duplicate_key = {**profile, "source_table": f"{common.UC_PREFIX}.another"}
        duplicate_pair = {**profile, "key": "another-key"}
        duplicate_config = {
            **profile,
            "key": "another-config",
            "source_table": f"{common.UC_PREFIX}.another_config",
            "index_name": f"{common.UC_PREFIX}.another_config_index",
        }
        invalid_values = (
            "not-json",
            "[]",
            json.dumps([{**profile, "source_table": "other.schema.source"}]),
            json.dumps([{**profile, "index_name": f"{common.UC_PREFIX}.{{variant_id}}"}]),
            json.dumps([profile, duplicate_key]),
            json.dumps([profile, duplicate_pair]),
            json.dumps([profile, duplicate_config]),
        )
        for raw in invalid_values:
            with self.subTest(raw=raw):
                with self.assertRaises(common.JobContractError):
                    common.load_index_profiles(raw)

    def test_all_profiles_respect_child_and_parent_boundaries(self) -> None:
        document = {
            "document_id": "doc-1",
            "title": "テスト資料",
            "summary": "チャンク境界の検証資料",
            "model": "Prius",
            "model_year": 2024,
            "document_type": "owners_guide",
            "vehicle_category": "passenger_car",
        }
        elements = [
            {"element_id": 1, "page_number": 1, "element_type": "title", "content": "テスト資料"},
            {"element_id": 2, "page_number": 1, "element_type": "section_header", "content": "安全装備"},
            {"element_id": 3, "page_number": 1, "element_type": "paragraph", "content": "安全確認を行います。" * 90},
            {"element_id": 4, "page_number": 2, "element_type": "section_header", "content": "操作方法"},
            {"element_id": 5, "page_number": 2, "element_type": "paragraph", "content": "スイッチを押します。" * 90},
        ]
        for method in ("STANDARD", "SEMANTIC", "PARENT_CHILD"):
            for size in (256, 512, 1024):
                with self.subTest(method=method, size=size):
                    configuration = {
                        "chunk_method": method,
                        "chunk_size_tokens": size,
                        "parent_chunk_size_tokens": min(size * 4, 4096)
                        if method == "PARENT_CHILD" else None,
                        "content_profile": "LAYOUT_PRESERVING",
                        "cleaning_enabled": True,
                        "semantic_metadata_enabled": True,
                        "embedding_model_key": common.BASELINE_EMBEDDING_MODEL_KEY,
                    }
                    chunks = common.build_document_chunks(
                        document,
                        elements,
                        configuration,
                        project_id=common.BASELINE_PROJECT_ID,
                        variant_id="550e8400-e29b-41d4-a716-446655440000",
                    )
                    self.assertTrue(chunks)
                    for chunk in chunks:
                        self.assertLessEqual(
                            common.estimate_token_count(chunk["chunk_to_retrieve"]),
                            size,
                        )
                        self.assertTrue(
                            set(chunk["page_numbers"]).issubset(
                                chunk["parent_page_numbers"]
                            )
                        )
                        if method == "PARENT_CHILD":
                            self.assertIsNotNone(chunk["parent_chunk_id"])

    def test_chunk_builder_allows_non_baseline_project(self) -> None:
        project_id = str(uuid.uuid4())
        variant_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        chunks = common.build_document_chunks(
            {
                "document_id": document_id,
                "title": "別Project資料",
                "model": "Prius",
                "model_year": 2024,
                "document_type": "owners_guide",
                "vehicle_category": "passenger_car",
            },
            [
                {
                    "element_id": 1,
                    "page_number": 1,
                    "element_type": "paragraph",
                    "content": "別Projectでも安全にチャンクを作成します。",
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
            project_id=project_id,
            variant_id=variant_id,
        )
        self.assertTrue(chunks)

    def test_validate_eval_batch_requires_one_immutable_config(self) -> None:
        run_id = str(uuid.uuid4())
        config = {
            "phase_ids": ["phase_01", "phase_02"],
            "trial_count": 3,
            "dataset_version": "v1.0.0",
            "dataset_split": "development",
            "variant_id": "default",
            "answer_model_key": "answer",
            "judge_model_key": "judge",
            "final_k": 10,
        }
        base = {
            "project_id": common.BASELINE_PROJECT_ID,
            "eval_run_id": run_id,
            "variant_id": "default",
            "dataset_version": "v1.0.0",
            "dataset_split": "development",
            "answer_model_key": "answer",
            "judge_model_key": "judge",
            "trial_count": 3,
            "config_json": common.canonical_json(config),
            "config_hash": common.canonical_json_hash(config),
        }
        rows = [{**base, "phase_id": "phase_01"}, {**base, "phase_id": "phase_02"}]
        validated = common.validate_eval_batch(rows)
        self.assertEqual(validated["phases"], ["phase_01", "phase_02"])
        rows[1] = {**rows[1], "dataset_split": "holdout"}
        with self.assertRaises(common.JobContractError):
            common.validate_eval_batch(rows)

    def test_validate_eval_batch_collapses_identical_concurrent_phase_rows(self) -> None:
        run_id = str(uuid.uuid4())
        config = {
            "phase_ids": ["phase_01"],
            "trial_count": 1,
            "dataset_version": "v1.0.0",
            "dataset_split": "development",
            "variant_id": "default",
            "answer_model_key": "answer",
            "judge_model_key": "judge",
            "final_k": 10,
        }
        row = {
            "project_id": common.BASELINE_PROJECT_ID,
            "eval_run_id": run_id,
            "phase_id": "phase_01",
            "variant_id": "default",
            "dataset_version": "v1.0.0",
            "dataset_split": "development",
            "answer_model_key": "answer",
            "judge_model_key": "judge",
            "trial_count": 1,
            "config_json": common.canonical_json(config),
            "config_hash": common.canonical_json_hash(config),
        }

        validated = common.validate_eval_batch([row, dict(row)])

        self.assertEqual(validated["phases"], ["phase_01"])

    def test_validate_eval_batch_allows_non_baseline_project_and_hidden_retry_key(self) -> None:
        project_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        variant_id = str(uuid.uuid4())
        public_config = {
            "phase_ids": ["phase_01"],
            "trial_count": 1,
            "dataset_version": "project-v2",
            "dataset_split": "development",
            "variant_id": variant_id,
            "answer_model_key": "answer",
            "judge_model_key": "judge",
            "final_k": 10,
        }
        stored_config = {**public_config, "_idempotency_key": "retry-12345678"}
        row = {
            "project_id": project_id,
            "eval_run_id": run_id,
            "phase_id": "phase_01",
            "variant_id": variant_id,
            "dataset_version": "project-v2",
            "dataset_split": "development",
            "answer_model_key": "answer",
            "judge_model_key": "judge",
            "trial_count": 1,
            "config_json": common.canonical_json(stored_config),
            "config_hash": common.canonical_json_hash(public_config),
        }
        validated = common.validate_eval_batch([row])
        self.assertEqual(validated["project_id"], project_id)

    def test_validate_eval_batch_accepts_selected_evaluation_case_ids(self) -> None:
        project_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        variant_id = str(uuid.uuid4())
        public_config = {
            "phase_ids": ["phase_01"],
            "trial_count": 1,
            "dataset_version": "project-v2",
            "dataset_split": "development",
            "evaluation_case_ids": ["case-1", "case-2"],
            "variant_id": variant_id,
            "answer_model_key": "answer",
            "judge_model_key": "judge",
            "final_k": 10,
        }
        row = {
            "project_id": project_id,
            "eval_run_id": run_id,
            "phase_id": "phase_01",
            "variant_id": variant_id,
            "dataset_version": "project-v2",
            "dataset_split": "development",
            "answer_model_key": "answer",
            "judge_model_key": "judge",
            "trial_count": 1,
            "config_json": common.canonical_json(public_config),
            "config_hash": common.canonical_json_hash(public_config),
        }
        validated = common.validate_eval_batch([row])
        self.assertEqual(
            validated["config"]["evaluation_case_ids"],
            ["case-1", "case-2"],
        )

        invalid_config = {**public_config, "evaluation_case_ids": ["case-1", "case-1"]}
        invalid_row = {
            **row,
            "config_json": common.canonical_json(invalid_config),
            "config_hash": common.canonical_json_hash(invalid_config),
        }
        with self.assertRaisesRegex(common.JobContractError, "evaluation_case_ids"):
            common.validate_eval_batch([invalid_row])

    def test_eval_case_scope_requires_project_dataset_and_non_empty_rows(self) -> None:
        project_id = str(uuid.uuid4())
        with self.assertRaisesRegex(common.JobContractError, "no evaluation cases"):
            common.validate_eval_cases(
                [],
                project_id=project_id,
                dataset_version="v2",
                dataset_split="development",
            )
        cases = common.validate_eval_cases(
            [
                {
                    "project_id": project_id,
                    "eval_case_id": "case-1",
                    "dataset_version": "v2",
                    "dataset_split": "development",
                }
            ],
            project_id=project_id,
            dataset_version="v2",
            dataset_split="development",
        )
        self.assertEqual(cases[0]["eval_case_id"], "case-1")

    def test_eval_ground_truth_rejects_cross_project_document_uri(self) -> None:
        with self.assertRaisesRegex(common.JobContractError, "outside"):
            common.validate_eval_case_document_scope(
                [
                    {
                        "relevant_doc_uri": "/Volumes/allowed.pdf",
                        "relevance_judgments": [
                            {
                                "doc_uri": "/Volumes/another-project.pdf",
                                "page_number": 1,
                                "relevance_grade": 3,
                            }
                        ],
                    }
                ],
                allowed_doc_uris={"/Volumes/allowed.pdf"},
            )

    def test_default_variant_never_falls_back_across_projects(self) -> None:
        project_id = str(uuid.uuid4())
        variant_id = str(uuid.uuid4())
        self.assertEqual(
            common.resolve_project_variant_id(
                project_id=project_id,
                requested_variant_id="default",
                active_variant_id=variant_id,
            ),
            variant_id,
        )
        with self.assertRaisesRegex(common.JobContractError, "another Project"):
            common.resolve_project_variant_id(
                project_id=project_id,
                requested_variant_id="default",
                active_variant_id=common.BASELINE_PHYSICAL_VARIANT_ID,
            )

    def test_project_and_variant_filters_are_mandatory_and_cannot_be_overridden(self) -> None:
        project_id = str(uuid.uuid4())
        variant_id = str(uuid.uuid4())
        merged = common.merge_project_variant_filters(
            project_id=project_id,
            variant_id=variant_id,
            metadata_filters={"document_id": [str(uuid.uuid4())]},
        )
        self.assertEqual(merged["project_id"], project_id)
        self.assertEqual(merged["variant_id"], variant_id)
        self.assertIn("document_id", merged)

        for attempted in (
            {"project_id": str(uuid.uuid4())},
            {"variant_id": str(uuid.uuid4())},
        ):
            with self.subTest(attempted=attempted):
                with self.assertRaises(common.JobContractError):
                    common.merge_project_variant_filters(
                        project_id=project_id,
                        variant_id=variant_id,
                        metadata_filters=attempted,
                    )

    def test_page_metrics_are_graded_and_page_level(self) -> None:
        qrels = [
            {"doc_uri": "doc-a", "page_number": 2, "relevance_grade": 3},
            {"doc_uri": "doc-b", "page_number": 4, "relevance_grade": 2},
        ]
        rows = [
            {"doc_uri": "doc-a", "page_numbers": [2]},
            {"doc_uri": "doc-b", "page_numbers": [4]},
            {"doc_uri": "doc-c", "page_numbers": [1]},
        ]
        metrics = common.page_metrics(qrels, rows, k=10)
        self.assertEqual(metrics["recall"], 1.0)
        self.assertEqual(metrics["precision"], 0.2)
        self.assertEqual(metrics["ndcg"], 1.0)
        self.assertEqual(metrics["chunk_page_coverage_recall"], 1.0)

    def test_filter_extraction_does_not_choose_a_year_for_comparison(self) -> None:
        master = [
            {
                "model": "Prius",
                "aliases": ["プリウス"],
                "valid_model_years": [2023, 2024],
            }
        ]
        filters = common.extract_validated_filters(
            "2023年式から2024年式のプリウスで何が変わりましたか。", master
        )
        self.assertEqual(filters, {"model": "Prius"})

    def test_search_parser_accepts_implicit_score_column(self) -> None:
        names = [
            "chunk_id", "project_id", "variant_id", "document_id",
            "chunk_to_retrieve", "doc_uri"
        ]
        raw = {
            "manifest": {"columns": [{"name": name} for name in names]},
            "result": {
                "data_array": [["c", "p", "v", "d", "body", "uri", 0.9]]
            },
        }
        rows, _ = common.parse_search_response(raw)
        self.assertEqual(rows[0]["score"], 0.9)

    def test_search_parser_rejects_index_without_variant_column(self) -> None:
        names = [
            "chunk_id", "project_id", "document_id", "chunk_to_retrieve", "doc_uri"
        ]
        raw = {
            "manifest": {"columns": [{"name": name} for name in names]},
            "result": {"data_array": [["c", "p", "d", "body", "uri"]]},
        }

        with self.assertRaisesRegex(
            common.JobContractError,
            "variant_id",
        ):
            common.parse_search_response(raw)

    def test_reranker_time_is_read_from_version_078_debug_shape(self) -> None:
        self.assertEqual(common.extract_reranker_ms({"reranker_time": 123.4}), 123.4)

    def test_sync_wait_checks_fixed_index_contract(self) -> None:
        class Service:
            synced = False

            def sync_index(self, index_name: str) -> None:
                self.synced = index_name == common.BASELINE_INDEX_NAME

            def get_index(self, index_name: str) -> dict:
                self.asserted_name = index_name
                return {
                    "name": common.BASELINE_INDEX_NAME,
                    "endpoint_name": common.BASELINE_SEARCH_ENDPOINT,
                    "primary_key": "chunk_id",
                    "index_type": "DELTA_SYNC",
                    "index_subtype": "HYBRID",
                    "delta_sync_index_spec": {
                        "pipeline_type": "TRIGGERED",
                        "source_table": common.BASELINE_CHUNK_TABLE,
                        "columns_to_sync": sorted(common.INDEX_SYNC_COLUMNS),
                        "embedding_source_columns": [{
                            "name": "chunk_to_embed",
                            "embedding_model_endpoint_name": (
                                common.BASELINE_EMBEDDING_ENDPOINT
                            ),
                            "model_endpoint_name_for_query": (
                                common.BASELINE_EMBEDDING_ENDPOINT
                            ),
                        }],
                    },
                    "status": {"ready": True, "indexed_row_count": 40},
                }

        class Workspace:
            vector_search_indexes = Service()

        result = common.trigger_index_sync_and_wait(Workspace(), poll_seconds=1)
        self.assertTrue(Workspace.vector_search_indexes.synced)
        self.assertEqual(result["status"]["indexed_row_count"], 40)

    def test_sync_wait_rejects_non_integer_or_negative_expected_rows(self) -> None:
        class Workspace:
            vector_search_indexes = object()

        for invalid in (True, False, -1, 1.5, "40", None):
            with self.subTest(expected_rows=invalid):
                with self.assertRaisesRegex(
                    common.JobContractError,
                    "non-negative integer",
                ):
                    common.trigger_index_sync_and_wait(
                        Workspace(),
                        expected_rows=invalid,  # type: ignore[arg-type]
                    )

    def test_index_sync_fails_before_mutation_when_existing_spec_is_wrong(self) -> None:
        class Service:
            sync_calls = 0

            def sync_index(self, index_name: str) -> None:
                del index_name
                self.sync_calls += 1

            def get_index(self, index_name: str) -> dict:
                del index_name
                return {
                    "name": common.BASELINE_INDEX_NAME,
                    "endpoint_name": common.BASELINE_SEARCH_ENDPOINT,
                    "primary_key": "chunk_id",
                    "index_type": "DELTA_SYNC",
                    "index_subtype": "HYBRID",
                    "delta_sync_index_spec": {
                        "pipeline_type": "TRIGGERED",
                        "source_table": f"{common.UC_PREFIX}.wrong_source",
                        "columns_to_sync": sorted(common.INDEX_SYNC_COLUMNS),
                        "embedding_source_columns": [{
                            "name": "chunk_to_embed",
                            "embedding_model_endpoint_name": (
                                common.BASELINE_EMBEDDING_ENDPOINT
                            ),
                        }],
                    },
                    "status": {"ready": True, "indexed_row_count": 40},
                }

        service = Service()

        class Workspace:
            vector_search_indexes = service

        with self.assertRaisesRegex(common.JobContractError, "another source table"):
            common.trigger_index_sync_and_wait(Workspace(), poll_seconds=1)
        self.assertEqual(service.sync_calls, 0)

    def test_existing_index_accepts_get_response_that_omits_columns_to_sync(self) -> None:
        profile = common.load_index_profiles("")[common.BASELINE_INDEX_PROFILE_KEY]
        payload = {
            "name": common.BASELINE_INDEX_NAME,
            "endpoint_name": common.BASELINE_SEARCH_ENDPOINT,
            "primary_key": "chunk_id",
            "index_type": "DELTA_SYNC",
            "index_subtype": "HYBRID",
            "delta_sync_index_spec": {
                "pipeline_type": "TRIGGERED",
                "source_table": common.BASELINE_CHUNK_TABLE,
                "embedding_source_columns": [{
                    "name": "chunk_to_embed",
                    "embedding_model_endpoint_name": common.BASELINE_EMBEDDING_ENDPOINT,
                }],
            },
        }

        self.assertEqual(
            common.validate_existing_index(payload, profile=profile),
            payload,
        )

    def test_existing_index_rejects_explicit_incomplete_columns_to_sync(self) -> None:
        profile = common.load_index_profiles("")[common.BASELINE_INDEX_PROFILE_KEY]
        payload = {
            "name": common.BASELINE_INDEX_NAME,
            "endpoint_name": common.BASELINE_SEARCH_ENDPOINT,
            "primary_key": "chunk_id",
            "index_type": "DELTA_SYNC",
            "index_subtype": "HYBRID",
            "delta_sync_index_spec": {
                "pipeline_type": "TRIGGERED",
                "source_table": common.BASELINE_CHUNK_TABLE,
                "columns_to_sync": ["project_id", "document_id"],
                "embedding_source_columns": [{
                    "name": "chunk_to_embed",
                    "embedding_model_endpoint_name": common.BASELINE_EMBEDDING_ENDPOINT,
                }],
            },
        }

        with self.assertRaisesRegex(
            common.JobContractError,
            "synchronized columns",
        ):
            common.validate_existing_index(payload, profile=profile)

    def test_existing_index_accepts_exact_columns_to_index_alias(self) -> None:
        profile = common.load_index_profiles("")[common.BASELINE_INDEX_PROFILE_KEY]
        payload = {
            "name": common.BASELINE_INDEX_NAME,
            "endpoint_name": common.BASELINE_SEARCH_ENDPOINT,
            "primary_key": "chunk_id",
            "index_type": "DELTA_SYNC",
            "index_subtype": "HYBRID",
            "delta_sync_index_spec": {
                "pipeline_type": "TRIGGERED",
                "source_table": common.BASELINE_CHUNK_TABLE,
                "columns_to_index": sorted(common.INDEX_SYNC_COLUMNS),
                "embedding_source_columns": [{
                    "name": "chunk_to_embed",
                    "embedding_model_endpoint_name": common.BASELINE_EMBEDDING_ENDPOINT,
                }],
            },
        }

        self.assertEqual(
            common.validate_existing_index(payload, profile=profile),
            payload,
        )


if __name__ == "__main__":
    unittest.main()
