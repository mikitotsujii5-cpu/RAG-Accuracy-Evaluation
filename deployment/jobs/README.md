# Lakeflow Jobs deployment inputs

These files are request bodies for Databricks CLI `v1.3.0` `jobs create`.
They contain placeholders for the target Workspace's run-as user and MLflow／
Warehouse values. Replace every `<...>` value locally before sending a request;
never commit the rendered payload. Compute differs by Job. The production Data
Preparation Job is Performance-optimized Serverless; Evaluation and Index Sync
keep the Classic Job compute declared in their own JSON files.

| Data Preparation setting | Value |
|---|---|
| Compute | Lakeflow Jobs Serverless |
| Performance target | `PERFORMANCE_OPTIMIZED` |
| Environment | key `toyota_rag_serverless_v5` / version `5` |
| Concurrent runs | `max_concurrent_runs=2` |
| Environment dependency | `databricks-sdk==0.135.0` |
| Job tag | `compute_profile=serverless-performance-optimized-v5` |

Serverless notebook tasks do not support task-level libraries. The pinned SDK
is therefore declared under the Job Environment `dependencies`, not under
`tasks[].libraries`. It provides the `IndexSubtype` API required to create a
HYBRID AI Search index. Two concurrent runs allow independent Variant rebuilds
to proceed in parallel. The managed AI Search Index create/sync stage still
runs outside the Job compute.

Performance optimized prioritizes startup latency. Standard performance mode
uses fewer DBUs and normally tolerates a 4-to-6-minute startup delay. Compare
the measured latency with `system.billing.usage` before changing this setting.

`ai_parse_document` also does not run on this Job compute in the current App.
The upload background task sends it to the X-Large Serverless SQL Warehouse by
using a `FILE` value from `READ_FILES(..., format => 'file')`. The Serverless
Data Preparation Job starts with the later `BUILD_VARIANT` work: chunking,
writing the source Delta Table, and requesting AI Search Index creation/sync.

The verified reference deployment completed both a 1-PDF canary and an
isolated 8-PDF canary with a READY Index. Keep concrete Job, run, task, prep,
Variant, deployment, Statement, and Trace IDs only in a private operations log;
do not add them to this repository.

The former Classic accelerated definition is retained as
`data_preparation_job_classic_fallback.json`: DBR `18.x-scala2.13`, driver
`Standard_D16s_v5`, two `Standard_D8s_v5` workers, and the SDK as a task
library. Keep run-specific success and failure evidence in the private
operations log; do not reinterpret the Classic task-library requirement as the
Serverless dependency format.

Import all four Python notebook sources to the same Workspace directory first.
The three executable notebooks use `%run ./job_common`, so relative placement
must be preserved.

Import only the four notebook sources. Do not use `import-dir`, because local
unit tests and Python cache files are not runtime dependencies.

```bash
export DATABRICKS_CONFIG_PROFILE="<DATABRICKS_CLI_PROFILE>"
export WORKSPACE_JOB_DIR="/Workspace/Users/<RUN_AS_USER_EMAIL>/toyota-rag-accuracy-eval/jobs"

databricks workspace import \
  "$WORKSPACE_JOB_DIR/job_common" \
  --file jobs/job_common.py --format SOURCE --language PYTHON --overwrite --profile "$DATABRICKS_CONFIG_PROFILE"
databricks workspace import \
  "$WORKSPACE_JOB_DIR/data_preparation_job" \
  --file jobs/data_preparation_job.py --format SOURCE --language PYTHON --overwrite --profile "$DATABRICKS_CONFIG_PROFILE"
databricks workspace import \
  "$WORKSPACE_JOB_DIR/evaluation_job" \
  --file jobs/evaluation_job.py --format SOURCE --language PYTHON --overwrite --profile "$DATABRICKS_CONFIG_PROFILE"
databricks workspace import \
  "$WORKSPACE_JOB_DIR/index_sync_job" \
  --file jobs/index_sync_job.py --format SOURCE --language PYTHON --overwrite --profile "$DATABRICKS_CONFIG_PROFILE"
```

Create each Job and save the returned `job_id`. Do not re-run these commands
when a matching Job already exists; use `jobs reset` with its existing ID
instead so App configuration does not silently point at a duplicate.

```bash
export DATA_PREPARATION_JOB_JSON="<RENDERED_DATA_PREPARATION_JOB_JSON>"
export EVALUATION_JOB_JSON="<RENDERED_EVALUATION_JOB_JSON>"
export INDEX_SYNC_JOB_JSON="<RENDERED_INDEX_SYNC_JOB_JSON>"

databricks jobs create --json @"$DATA_PREPARATION_JOB_JSON" --profile "$DATABRICKS_CONFIG_PROFILE"
databricks jobs create --json @"$EVALUATION_JOB_JSON" --profile "$DATABRICKS_CONFIG_PROFILE"
databricks jobs create --json @"$INDEX_SYNC_JOB_JSON" --profile "$DATABRICKS_CONFIG_PROFILE"
```

For an existing Data Preparation Job, do not pass the settings file directly
to `jobs reset`. Wrap it with the existing `job_id` and `new_settings`, then
reset the same Job ID:

```bash
export DATA_PREPARATION_JOB_ID="<DATA_PREPARATION_JOB_ID>"

jq --argjson job_id "$DATA_PREPARATION_JOB_ID" \
  '{job_id: $job_id, new_settings: .}' \
  "$DATA_PREPARATION_JOB_JSON" \
  > /tmp/toyota_data_preparation_serverless_reset.json

databricks jobs reset \
  --json @/tmp/toyota_data_preparation_serverless_reset.json \
  --profile "$DATABRICKS_CONFIG_PROFILE"
```

If a Serverless compatibility issue is confirmed, first let active preparation
runs reach a terminal state. Then use the checked-in Classic fallback through
the same wrapper. This keeps the existing Job ID, so the App's `PREP_JOB_ID`
does not change:

```bash
export DATA_PREPARATION_CLASSIC_JOB_JSON="<RENDERED_DATA_PREPARATION_CLASSIC_JOB_JSON>"

jq --argjson job_id "$DATA_PREPARATION_JOB_ID" \
  '{job_id: $job_id, new_settings: .}' \
  "$DATA_PREPARATION_CLASSIC_JOB_JSON" \
  > /tmp/toyota_data_preparation_classic_rollback.json

databricks jobs reset \
  --json @/tmp/toyota_data_preparation_classic_rollback.json \
  --profile "$DATABRICKS_CONFIG_PROFILE"

databricks jobs get "$DATA_PREPARATION_JOB_ID" --profile "$DATABRICKS_CONFIG_PROFILE" -o json
```

After rollback, run a new isolated smoke input and require the prep run to be
`SUCCEEDED`, its source and Index row counts to match, and the Index to be
`READY`. Do not reactivate a failed or superseded Variant manually.

Set the data-preparation and evaluation IDs as `PREP_JOB_ID` and `EVAL_JOB_ID`
in the Databricks App environment. The App passes only `prep_run_id` or
`eval_run_id`; physical resource names remain server-side constants.

The Evaluation Job additionally requires `MLFLOW_EXPERIMENT_PATH`,
`MLFLOW_EXPERIMENT_ID`, and `MLFLOW_TRACING_SQL_WAREHOUSE_ID`. Replace those
placeholders in a local copy of `evaluation_job.json` before creating or
resetting the Job.

The preparation Job supports all nine method/size profiles (`STANDARD`,
`SEMANTIC`, `PARENT_CHILD` × 256/512/1024) and every READY/selectable FMAPI
Embedding entry in the model catalog. It reads `project_id` and `document_ids`
only from the persisted, hash-verified run row and rejects missing or
cross-Project documents. Each source Table and HYBRID Delta Sync Index is
deterministically derived from the server-generated Variant UUID. After the
Index becomes READY, the Job grants only `SELECT` on that derived Index to the
deployed App service principal; it does not grant access to every future Table.
The same successful run idempotently creates three `starter-v1` questions in
the Project's Delta evaluation table. They reference a Project PDF but leave
unknown answer/page labels empty; the evaluation Job therefore records NULL
for answer-correctness and page-level retrieval metrics until human-labelled
cases are added.

The single-PDF logical-deletion workflow also uses the same Data Preparation
Job. Before submission, the App reconstructs each affected Variant's verified
configuration and replaces its source-document list with the remaining PDFs
from that Variant. The Job validates the persisted hash and writes a new,
immutable source Table and AI Search Index; it never edits the superseded
Variant in place. Only the successor of the previously active Variant uses the
default `activate_on_success=true`. Other successors persist the server-only
`activate_on_success=false` flag in the verified configuration, so parallel
rebuild completion cannot overwrite the Project's active Variant pointer. If a
Variant has no remaining source PDF, no empty successor is submitted.

Whenever `jobs/data_preparation_job.py` or `jobs/job_common.py` changes for this
workflow, re-import both files (and, by policy, all four notebook sources), then
reset the existing Data Preparation Job ID. A deletion E2E is not complete
until the successor Job succeeds, the new Index is READY, and its source and
indexed rows contain no chunk from the logically deleted document.

The evaluation Job resolves cases from the persisted run's Project,
`dataset_version`, and split. An empty or cross-Project dataset fails before any
trial runs. Re-import all notebook sources and reset the existing Jobs whenever
the checked-in implementation changes.

Before importing the updated notebooks, run `sql/01_foundation.sql`,
`sql/02_seed_core.sql`, `sql/08_seed_starter_evaluation.sql`, and
`deployment/app_uc_grants.sql` in that order. The first two create and seed the
separate model-default policy table; the third backfills existing Projects.

The FMAPI request intentionally omits the optional `temperature` setting.
Supported parameters differ by endpoint; for example, GPT-5.6 Luna accepts only
its default temperature. Keep the model catalog as the endpoint allow-list, and
add an optional generation parameter only after validating that capability for
the selected endpoint.

Official Azure Databricks documentation:

- [Run Lakeflow Jobs with serverless compute](https://learn.microsoft.com/en-us/azure/databricks/jobs/run-serverless-jobs)
- [Serverless environment and dependencies](https://learn.microsoft.com/en-us/azure/databricks/compute/serverless/dependencies)
- [Serverless compute limitations](https://learn.microsoft.com/en-us/azure/databricks/compute/serverless/limitations)
- [Migrate jobs to serverless compute](https://learn.microsoft.com/en-us/azure/databricks/compute/serverless/migration)
