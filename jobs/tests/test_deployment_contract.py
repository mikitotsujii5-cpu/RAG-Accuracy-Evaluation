from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]


class DeploymentContractTest(unittest.TestCase):
    @staticmethod
    def _load_script(filename: str) -> Any:
        module_path = REPO / "scripts" / filename
        spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), module_path)
        if spec is None or spec.loader is None:
            raise AssertionError(f"cannot load {filename}")
        module = importlib.util.module_from_spec(spec)
        scripts_path = str(module_path.parent)
        sys.path.insert(0, scripts_path)
        try:
            spec.loader.exec_module(module)
        finally:
            sys.path.remove(scripts_path)
        return module

    def test_sql_splitter_ignores_quotes_and_semicolons_inside_comments(self) -> None:
        module_path = REPO / "scripts" / "execute_sql_file.py"
        spec = importlib.util.spec_from_file_location("execute_sql_file", module_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        sql = """-- Delta's migration; keep this comment\nSELECT 'it''s; valid';\n/* comment's ; quote */\nSELECT 2;\n-- trailing deployment note without SQL"""
        statements = module.split_sql(sql)

        self.assertEqual(len(statements), 2)
        self.assertIn("SELECT 'it''s; valid'", statements[0])
        self.assertIn("SELECT 2", statements[1])

    def test_model_catalog_sync_disables_stale_fmapi_rows(self) -> None:
        source = (REPO / "scripts" / "sync_model_catalog.py").read_text(encoding="utf-8")
        self.assertIn("WHEN NOT MATCHED BY SOURCE", source)
        self.assertIn("target.selectable = FALSE", source)
        self.assertIn("target.endpoint_state = 'UNAVAILABLE'", source)

    def test_embedding_default_policy_prefers_verified_qwen_and_has_fallback(self) -> None:
        foundation = (REPO / "sql" / "01_foundation.sql").read_text(encoding="utf-8")
        seed = (REPO / "sql" / "02_seed_core.sql").read_text(encoding="utf-8")
        config = json.loads((REPO / "config" / "field-eng-east.json").read_text())
        grants = (REPO / "deployment" / "app_uc_grants.sql").read_text(encoding="utf-8")

        self.assertIn("toyota_rag_model_defaults", foundation)
        self.assertIn("'emb-qwen3-0-6b' AS preferred_model_key", seed)
        self.assertIn("READY_SELECTABLE_DISPLAY_NAME_MODEL_KEY_ASC", seed)
        self.assertEqual(config["default_embedding_model_key"], "emb-qwen3-0-6b")
        self.assertEqual(
            config["embedding_endpoint"],
            "databricks-qwen3-embedding-0-6b",
        )
        self.assertIn("GRANT SELECT ON TABLE rag_accuracy_demo.rag_accuracy.toyota_rag_model_defaults", grants)

    def test_existing_and_future_projects_get_persisted_starter_questions(self) -> None:
        backfill = (REPO / "sql" / "08_seed_starter_evaluation.sql").read_text(
            encoding="utf-8"
        )
        preparation = (REPO / "jobs" / "data_preparation_job.py").read_text(
            encoding="utf-8"
        )
        for marker in (
            "starter-sample-",
            "starter_sample_unlabeled",
            "CAST(array() AS ARRAY<INT>)",
            "LEFT ANTI JOIN",
        ):
            self.assertIn(marker, backfill)
        self.assertIn("build_starter_evaluation_cases", preparation)
        self.assertIn("MERGE INTO {EVAL_CASES_TABLE}", preparation)
        self.assertIn("active_dataset_version=coalesce", preparation)

    def test_failed_activating_rebuild_cannot_leave_project_rebuilding_forever(self) -> None:
        preparation = (REPO / "jobs" / "data_preparation_job.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("if bool(locals().get(\"activate_on_success\", False))", preparation)
        self.assertIn("SET status='NEEDS_BUILD'", preparation)
        self.assertIn("status='REBUILDING' AND active_variant_id IS NULL", preparation)
        self.assertIn("AND status NOT IN ('ARCHIVED', 'DELETING')", preparation)

    def test_last_document_companion_smoke_has_destructive_safety_and_audit_checks(self) -> None:
        path = REPO / "scripts" / "smoke_test_last_document_deletion.py"
        source = path.read_text(encoding="utf-8")
        ast.parse(source, filename=str(path))
        for marker in (
            '"--execute-delete"',
            '"--confirm-project-id"',
            "DISPOSABLE_PROJECT_PREFIX",
            '"PREFLIGHT_SUCCEEDED"',
            'payload.get("corpus_empty") is not True',
            'payload.get("rebuilds") != []',
            'str(empty_project.get("status") or "").upper() != "EMPTY"',
            'empty_project.get("active_variant_id") is not None',
            'if ready_variant_count != 0',
            'if prep_count_after != prep_count_before',
            'project_row[2] is not None',
            'if replay.get("already_deleted") is not True',
            "require_pdf_response(",
            "require_history_citation(",
            "status_out=delete_http_statuses",
            "require_delete_http_accepted(delete_http_statuses)",
            '"delete_http_status": delete_http_status',
            "ExecuteStatementRequestOnWaitTimeout.CONTINUE",
        ):
            self.assertIn(marker, source)

    def test_last_document_companion_requires_initial_delete_http_202(self) -> None:
        module = self._load_script("smoke_test_last_document_deletion.py")

        self.assertEqual(module.require_delete_http_accepted([202]), 202)
        for statuses in ([], [200], [204], [202, 202]):
            with self.subTest(statuses=statuses):
                with self.assertRaises(module.SmokeFailure):
                    module.require_delete_http_accepted(statuses)

    def test_document_deletion_smoke_is_fresh_e2e_and_emits_auditable_evidence(self) -> None:
        path = REPO / "scripts" / "smoke_test_document_deletion.py"
        source = path.read_text(encoding="utf-8")
        ast.parse(source, filename=str(path))

        for marker in (
            '"--pdf-to-delete", type=Path, required=True',
            '"--pdf-to-keep", type=Path, required=True',
            'status_out: list[int] | None = None',
            'status_out.append(int(response.status_code))',
            'initial_delete_http_status != [202]',
            '"initial_prep_run_id"',
            '"initial_job_run_id"',
            '"initial_delete_http_status"',
            '"successor_prep_run_id"',
            '"successor_job_run_id"',
            '"successor_preparation_runs"',
            '"old_variant_state"',
            '"document_registry_state"',
            '"replacement_indexed_rows"',
            '"retained_source_rows"',
            '"ai_search_retained_hits"',
            '"prior_session_id"',
            '"prior_request_id"',
            '"prior_trace_id"',
            '"successor_session_id"',
            '"successor_request_id"',
            '"successor_trace_id"',
            'workspace.vector_search_indexes.get_index(',
            "ExecuteStatementRequestOnWaitTimeout.CONTINUE",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("--resume-", source)

    def test_document_deletion_api_json_captures_success_status(self) -> None:
        module = self._load_script("smoke_test_document_deletion.py")

        class FakeResponse:
            status_code = 202

            @staticmethod
            def json() -> dict[str, bool]:
                return {"deleted": True}

        class FakeSession:
            @staticmethod
            def request(*args: Any, **kwargs: Any) -> FakeResponse:
                return FakeResponse()

        statuses: list[int] = []
        payload = module.api_json(
            FakeSession(),
            "DELETE",
            "https://example.invalid/api/document",
            status_out=statuses,
        )
        self.assertEqual(payload, {"deleted": True})
        self.assertEqual(statuses, [202])

    def test_phase_advisor_receives_answer_quality_and_judge_rationales(self) -> None:
        source = (REPO / "jobs" / "evaluation_job.py").read_text(encoding="utf-8")
        for marker in (
            '"answer_correctness": average("answer_correctness")',
            '"groundedness": average("groundedness")',
            '"citation_correctness": average("citation_correctness")',
            '"assessment_rationales": {',
        ):
            self.assertIn(marker, source)

    def test_app_update_preserves_all_resource_bindings_and_user_scope(self) -> None:
        create = json.loads(
            (REPO / "deployment" / "app_create.json").read_text(encoding="utf-8")
        )
        update = json.loads(
            (REPO / "deployment" / "app_resources_update.json").read_text(
                encoding="utf-8"
            )
        )
        user_scope_update = json.loads(
            (REPO / "deployment" / "app_user_scopes_update.json").read_text(
                encoding="utf-8"
            )
        )
        expected_names = {
            "app-warehouse",
            "toyota-volume",
            "baseline-index",
            "default-llm",
            "prep-job",
            "eval-job",
            "mlflow-experiment",
        }
        for payload in (create, update, user_scope_update):
            with self.subTest(payload=payload.get("description")):
                self.assertEqual(
                    {item["name"] for item in payload["resources"]}, expected_names
                )
                self.assertEqual(payload["user_api_scopes"], ["model-serving"])
        self.assertNotIn("compute_size", update)
        self.assertNotIn("compute_size", user_scope_update)
        self.assertEqual(update, user_scope_update)
        create_without_compute = dict(create)
        create_without_compute.pop("compute_size")
        self.assertEqual(create_without_compute, update)

        app_yaml = (REPO / "app" / "app.yaml").read_text(encoding="utf-8")
        value_from_names = {
            line.split(":", 1)[1].strip()
            for line in app_yaml.splitlines()
            if line.strip().startswith("valueFrom:")
        }
        self.assertEqual(value_from_names, expected_names)

        sensitive_keys = {
            "access_token", "authorization", "client_secret", "password",
            "private_key", "api_key",
        }

        def keys(value: Any) -> set[str]:
            if isinstance(value, dict):
                return {
                    *(str(key).casefold() for key in value),
                    *(nested for item in value.values() for nested in keys(item)),
                }
            if isinstance(value, list):
                return {nested for item in value for nested in keys(item)}
            return set()

        for payload in (create, update, user_scope_update):
            self.assertFalse(keys(payload).intersection(sensitive_keys))

    def test_app_can_read_and_write_project_evaluation_cases(self) -> None:
        grants = (REPO / "deployment" / "app_uc_grants.sql").read_text(
            encoding="utf-8"
        )
        table = "rag_accuracy_demo.rag_accuracy.toyota_rag_eval_cases"
        self.assertIn(f"GRANT SELECT ON TABLE {table}", grants)
        self.assertIn(f"GRANT MODIFY ON TABLE {table}", grants)
        self.assertNotIn("REVOKE ", grants.upper())
        self.assertNotIn("DROP ", grants.upper())

    def test_app_can_supersede_index_variants_for_document_deletion(self) -> None:
        grants = (REPO / "deployment" / "app_uc_grants.sql").read_text(
            encoding="utf-8"
        )
        table = "rag_accuracy_demo.rag_accuracy.toyota_index_variants"
        self.assertIn(f"GRANT SELECT ON TABLE {table}", grants)
        self.assertIn(f"GRANT MODIFY ON TABLE {table}", grants)
        self.assertNotIn("REVOKE ", grants.upper())
        self.assertNotIn("DROP ", grants.upper())

    def test_generic_smoke_tests_reject_untrusted_origins_and_cover_e2e_contract(self) -> None:
        workspace_host = "https://adb-123456789012345.11.azuredatabricks.net"
        valid_app = (
            "https://toyota-rag-accuracy-eval-123456789012345.11."
            "azure.databricksapps.com"
        )
        for filename in (
            "smoke_test_generic_upload.py",
            "smoke_test_generic_evaluation.py",
        ):
            with self.subTest(filename=filename):
                module = self._load_script(filename)
                self.assertEqual(
                    module.validate_app_base(valid_app + "/", workspace_host),
                    valid_app,
                )
                for untrusted in (
                    "http://toyota-rag-accuracy-eval-123456789012345.11.azure.databricksapps.com",
                    "https://attacker.example",
                    "https://user:secret@toyota-rag-accuracy-eval-123456789012345.11.azure.databricksapps.com",
                    "https://another-app-999999999999999.11.azure.databricksapps.com",
                    "https://evil-123456789012345-999999999999999.11.azure.databricksapps.com",
                    valid_app + "/api/health",
                    valid_app + "?access_token=must-not-be-accepted",
                ):
                    with self.assertRaises(RuntimeError):
                        module.validate_app_base(untrusted, workspace_host)

        upload_source = (REPO / "scripts" / "smoke_test_generic_upload.py").read_text(
            encoding="utf-8"
        )
        evaluation_source = (
            REPO / "scripts" / "smoke_test_generic_evaluation.py"
        ).read_text(encoding="utf-8")
        for marker in (
            '"custom_metadata"',
            '"model", "model_year", "document_type", "vehicle_category"',
            "page_count",
        ):
            self.assertIn(marker, upload_source)
        for marker in (
            "/documents",
            "/variants",
            '"PARSED"',
            '"READY"',
            '"answer_correctness"',
            '"groundedness"',
            '"citation_correctness"',
            'item.get("generation") != "LLM"',
        ):
            self.assertIn(marker, evaluation_source)
        self.assertIn("range(1, 6)", evaluation_source)
        self.assertNotIn("session.headers.update(workspace.config.authenticate())", upload_source)
        self.assertNotIn("session.headers.update(workspace.config.authenticate())", evaluation_source)

    def test_notebook_sources_are_valid_python(self) -> None:
        for path in sorted((REPO / "jobs").glob("*.py")):
            with self.subTest(path=path.name):
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                self.assertTrue(
                    path.read_text(encoding="utf-8").startswith("# Databricks notebook source")
                )

    def test_job_create_payloads_use_requested_compute_and_principal(self) -> None:
        expected_parameters = {
            "data_preparation_job.json": {"prep_run_id"},
            "data_preparation_job_classic_fallback.json": {"prep_run_id"},
            "evaluation_job.json": {"eval_run_id"},
            "index_sync_job.json": set(),
        }
        for filename, parameter_names in expected_parameters.items():
            path = REPO / "deployment" / "jobs" / filename
            with self.subTest(path=filename):
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(
                    payload["run_as"]["user_name"], "<RUN_AS_USER_EMAIL>"
                )
                self.assertEqual(
                    {item["name"] for item in payload.get("parameters", [])},
                    parameter_names,
                )
                task = payload["tasks"][0]
                self.assertIn("/jobs/", task["notebook_task"]["notebook_path"])
                expected_base_parameters = {
                    parameter_name: (
                        "{{job.parameters." + parameter_name + "}}"
                    )
                    for parameter_name in parameter_names
                }
                if filename == "evaluation_job.json":
                    expected_base_parameters.update(
                        {
                            "MLFLOW_EXPERIMENT_PATH": "<MLFLOW_EXPERIMENT_PATH>",
                            "MLFLOW_EXPERIMENT_ID": "<MLFLOW_EXPERIMENT_ID>",
                            "MLFLOW_TRACING_SQL_WAREHOUSE_ID": "<SQL_WAREHOUSE_ID>",
                        }
                    )
                self.assertEqual(
                    task["notebook_task"].get("base_parameters", {}),
                    expected_base_parameters,
                )
                if filename != "data_preparation_job_classic_fallback.json":
                    expected_serverless = {
                        "data_preparation_job.json": (
                            "toyota_rag_serverless_v5",
                            ["databricks-sdk==0.135.0"],
                        ),
                        "evaluation_job.json": (
                            "toyota_rag_evaluation_serverless_v5",
                            [
                                "databricks-sdk==0.135.0",
                                "databricks-ai-search==0.78",
                            ],
                        ),
                        "index_sync_job.json": (
                            "toyota_rag_index_sync_serverless_v5",
                            ["databricks-sdk==0.135.0"],
                        ),
                    }
                    environment_key, dependencies = expected_serverless[filename]
                    self.assertEqual(
                        payload["max_concurrent_runs"],
                        2 if filename == "data_preparation_job.json" else 1,
                    )
                    self.assertEqual(
                        payload["performance_target"], "PERFORMANCE_OPTIMIZED"
                    )
                    self.assertEqual(
                        payload["environments"],
                        [
                            {
                                "environment_key": environment_key,
                                "spec": {
                                    "environment_version": "5",
                                    "dependencies": dependencies,
                                },
                            }
                        ],
                    )
                    self.assertEqual(task["environment_key"], environment_key)
                    self.assertEqual(
                        payload["tags"]["compute_profile"],
                        "serverless-performance-optimized-v5",
                    )
                    self.assertNotIn("job_clusters", payload)
                    for compute_key in (
                        "job_cluster_key",
                        "existing_cluster_id",
                        "new_cluster",
                        "libraries",
                    ):
                        self.assertNotIn(compute_key, task)
                else:
                    cluster = payload["job_clusters"][0]["new_cluster"]
                    self.assertEqual(cluster["spark_version"], "18.x-scala2.13")
                    self.assertEqual(cluster["data_security_mode"], "USER_ISOLATION")
                    self.assertEqual(payload["max_concurrent_runs"], 2)
                    self.assertEqual(cluster["node_type_id"], "Standard_D8s_v5")
                    self.assertEqual(
                        cluster["driver_node_type_id"], "Standard_D16s_v5"
                    )
                    self.assertEqual(cluster["num_workers"], 2)
                    self.assertEqual(
                        task["libraries"][0]["pypi"]["package"],
                        "databricks-sdk==0.135.0",
                    )
                    self.assertEqual(
                        cluster["custom_tags"]["compute_profile"],
                        "accelerated-data-preparation",
                    )
                if filename == "evaluation_job.json":
                    self.assertEqual(
                        payload["environments"][0]["spec"]["dependencies"],
                        ["databricks-sdk==0.135.0", "databricks-ai-search==0.78"],
                    )
                    self.assertEqual(
                        task["notebook_task"]["base_parameters"][
                            "MLFLOW_TRACING_SQL_WAREHOUSE_ID"
                        ],
                        "<SQL_WAREHOUSE_ID>",
                    )
                    self.assertEqual(
                        task["notebook_task"]["base_parameters"]["MLFLOW_EXPERIMENT_ID"],
                        "<MLFLOW_EXPERIMENT_ID>",
                    )
                    self.assertEqual(
                        task["notebook_task"]["base_parameters"]["MLFLOW_EXPERIMENT_PATH"],
                        "<MLFLOW_EXPERIMENT_PATH>",
                    )
                elif filename == "index_sync_job.json":
                    self.assertEqual(
                        payload["environments"][0]["spec"]["dependencies"],
                        ["databricks-sdk==0.135.0"],
                    )

    def test_data_preparation_pins_sdk_for_existing_index_sync_only(self) -> None:
        payload = json.loads(
            (REPO / "deployment" / "jobs" / "data_preparation_job.json").read_text(
                encoding="utf-8"
            )
        )
        task = payload["tasks"][0]
        self.assertEqual(
            payload["environments"][0]["spec"]["dependencies"],
            ["databricks-sdk==0.135.0"],
        )
        self.assertNotIn("libraries", task)
        fallback = json.loads(
            (
                REPO
                / "deployment"
                / "jobs"
                / "data_preparation_job_classic_fallback.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            fallback["tasks"][0]["libraries"],
            [{"pypi": {"package": "databricks-sdk==0.135.0"}}],
        )
        source = (REPO / "jobs" / "data_preparation_job.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("WorkspaceClient", source)
        for creation_type in (
            "DeltaSyncVectorIndexSpecRequest",
            "EmbeddingSourceColumn",
            "IndexSubtype",
            "PipelineType",
            "VectorIndexType",
            "ResourceAlreadyExists",
        ):
            self.assertNotIn(creation_type, source)
        preparation_smoke = (
            REPO / "scripts" / "smoke_test_preparation.py"
        ).read_text(encoding="utf-8")
        self.assertIn('/api/index-profiles', preparation_smoke)
        self.assertIn('"index_profile_key": profile["profile_key"]', preparation_smoke)

    def test_app_and_jobs_never_create_delete_or_grant_physical_resources(self) -> None:
        """Deployment runtime may write rows and sync, but never mutate infrastructure."""

        runtime_paths = [
            *(REPO / "app").glob("*.py"),
            *(REPO / "jobs").glob("*.py"),
        ]
        forbidden_calls = {
            "create_index",
            "delete_index",
            "create_endpoint",
            "delete_endpoint",
            # saveAsTable creates a physical table when it is absent. Existing
            # profile tables must instead be validated before scoped writes.
            "saveAsTable",
        }
        forbidden_sql = re.compile(
            r"\b(?:GRANT\s+(?:SELECT|ALL|MODIFY|USE|READ|WRITE)|REVOKE\b|"
            r"CREATE\s+(?:TABLE|SCHEMA|CATALOG)|DROP\s+(?:TABLE|SCHEMA|CATALOG)|"
            r"ALTER\s+TABLE)\b",
            re.IGNORECASE,
        )
        violations: list[str] = []
        for path in runtime_paths:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in forbidden_calls
                ):
                    violations.append(f"{path.name}:{node.lineno}:{node.func.attr}")
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if forbidden_sql.search(node.value):
                        violations.append(f"{path.name}:{node.lineno}:physical SQL DDL/DCL")
                if not isinstance(node, ast.Call) or len(node.args) < 2:
                    continue
                method = node.args[0]
                route = node.args[1]
                if not isinstance(method, ast.Constant) or not isinstance(method.value, str):
                    continue
                route_text = "".join(
                    part.value
                    for part in ast.walk(route)
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)
                )
                if "vector-search/indexes" not in route_text:
                    continue
                http_method = method.value.upper()
                if http_method == "DELETE" or (
                    http_method == "POST" and "/sync" not in route_text
                ):
                    violations.append(
                        f"{path.name}:{node.lineno}:{http_method} {route_text}"
                    )
        self.assertEqual(violations, [])

    def test_runtime_has_no_uuid_derived_physical_resource_names(self) -> None:
        runtime_source = "\n".join(
            path.read_text(encoding="utf-8")
            for root in (REPO / "app", REPO / "jobs")
            for path in root.glob("*.py")
        )
        for marker in (
            "variant_resource_names",
            "_DYNAMIC_TABLE_SEGMENT",
            "SAFE_DYNAMIC_CHUNK_TABLE",
            'variant_id.replace("-", "")',
            "source_table + \"_index\"",
            "app_index_select_grant_sql",
        ):
            self.assertNotIn(marker, runtime_source)

    def test_preparation_replaces_only_one_logical_slice_atomically(self) -> None:
        source = (REPO / "jobs" / "data_preparation_job.py").read_text(
            encoding="utf-8"
        )

        # The administrator-owned Table is updated in one Delta transaction.
        # A DELETE followed by append could expose an empty corpus if the
        # second operation failed, and insertInto/saveAsTable could create or
        # evolve resources outside the manual provisioning contract.
        self.assertIn("MERGE INTO {source_table} AS target", source)
        self.assertIn("USING _prepared_chunk_source AS source", source)
        self.assertIn("WHEN NOT MATCHED BY SOURCE", source)
        self.assertIn(
            "AND target.project_id={sql_string(project_id)}",
            source,
        )
        self.assertIn(
            "AND target.variant_id={sql_string(target_variant_id)}",
            source,
        )
        self.assertNotRegex(source, r"DELETE\s+FROM\s+\{source_table\}")
        self.assertNotIn(".mode(\"append\")", source)
        self.assertNotIn(".insertInto(source_table)", source)

    def test_manual_infrastructure_files_are_not_app_runtime_inputs(self) -> None:
        """Manual SQL/spec files may describe resources but App startup cannot run them."""

        app_yaml = (REPO / "app" / "app.yaml").read_text(encoding="utf-8")
        self.assertNotIn("app_uc_grants.sql", app_yaml)
        self.assertNotIn("ai_search_index.json", app_yaml)
        self.assertNotIn("ai_search_endpoint.json", app_yaml)
        for source_path in (REPO / "app").glob("*.py"):
            source = source_path.read_text(encoding="utf-8")
            self.assertNotIn("deployment/app_uc_grants.sql", source)
            self.assertNotIn("deployment/ai_search_index.json", source)
            self.assertNotIn("deployment/ai_search_endpoint.json", source)

    def test_evaluation_fmapi_call_uses_cross_model_defaults(self) -> None:
        """Do not send optional sampling values rejected by some FMAPI models."""

        path = REPO / "jobs" / "evaluation_job.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "query"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "serving_endpoints"
        ]
        self.assertEqual(len(calls), 1)
        self.assertNotIn(
            "temperature",
            {keyword.arg for keyword in calls[0].keywords if keyword.arg is not None},
        )

    def test_evaluation_trials_emit_required_mlflow_trace_contract(self) -> None:
        path = REPO / "jobs" / "evaluation_job.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        def span_spec(node: ast.With) -> tuple[str, str] | None:
            if len(node.items) != 1 or not isinstance(node.items[0].context_expr, ast.Call):
                return None
            call = node.items[0].context_expr
            if not isinstance(call.func, ast.Attribute) or call.func.attr != "start_span":
                return None
            keywords = {item.arg: item.value for item in call.keywords if item.arg}
            name = keywords.get("name")
            span_type = keywords.get("span_type")
            if not isinstance(name, ast.Constant) or not isinstance(name.value, str):
                return None
            if (
                not isinstance(span_type, ast.Attribute)
                or not isinstance(span_type.value, ast.Name)
                or span_type.value.id != "SpanType"
            ):
                return None
            return name.value, span_type.attr

        spans = [
            (node, spec)
            for node in ast.walk(tree)
            if isinstance(node, ast.With) and (spec := span_spec(node)) is not None
        ]
        roots = [node for node, spec in spans if spec == ("offline_evaluation_case", "AGENT")]
        self.assertEqual(len(roots), 1)
        nested_specs = {
            spec
            for node in ast.walk(roots[0])
            if isinstance(node, ast.With) and (spec := span_spec(node)) is not None
        }
        self.assertEqual(
            nested_specs,
            {
                ("offline_evaluation_case", "AGENT"),
                ("final_retrieval", "RETRIEVER"),
                ("answer_generation", "CHAT_MODEL"),
                ("answer_judge", "EVALUATOR"),
            },
        )

        dimensions_fn = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "evaluation_trace_dimensions"
        )
        dimensions_return = next(
            node for node in ast.walk(dimensions_fn) if isinstance(node, ast.Return)
        )
        self.assertIsInstance(dimensions_return.value, ast.Dict)
        dimension_keys = {
            key.value
            for key in dimensions_return.value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        self.assertEqual(
            dimension_keys,
            {"project_id", "eval_run_id", "eval_case_id", "phase_id", "variant_id"},
        )

        root_calls = [node for node in ast.walk(roots[0]) if isinstance(node, ast.Call)]
        self.assertTrue(
            any(
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "update_current_trace"
                and any(
                    keyword.arg == "tags"
                    and isinstance(keyword.value, ast.Name)
                    and keyword.value.id == "trace_dimensions"
                    for keyword in call.keywords
                )
                for call in root_calls
            )
        )
        self.assertTrue(
            any(
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "payload"
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "trace_id"
                    for target in node.targets
                )
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "require_recording_trace_id"
                for node in ast.walk(roots[0])
            )
        )

        trace_output_fn = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "evaluation_trace_output"
        )
        traced_payload_nodes: list[ast.AST] = [trace_output_fn]
        traced_payload_nodes.extend(
            call
            for call in root_calls
            if isinstance(call.func, ast.Attribute)
            and call.func.attr in {"set_inputs", "set_outputs"}
        )
        trace_literal_keys = {
            key.value.casefold()
            for owner in traced_payload_nodes
            for mapping in ast.walk(owner)
            if isinstance(mapping, ast.Dict)
            for key in mapping.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        self.assertFalse(
            {
                key
                for key in trace_literal_keys
                if "access_token" in key or "authorization" in key or "bearer" in key
            }
        )


if __name__ == "__main__":
    unittest.main()
