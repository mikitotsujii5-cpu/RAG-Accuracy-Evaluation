# Databricks notebook source
# MAGIC %run ./job_common

# COMMAND ----------

"""Synchronize the manually created baseline Delta Sync Index."""

import json

from databricks.sdk import WorkspaceClient
from pyspark.sql import functions as F


# Verify the code-owned allow-list and registry before touching AI Search. This
# Job accepts no browser-controlled table, Index, endpoint, or Project name.
profiles = load_index_profiles()
profile = profiles.get(BASELINE_INDEX_PROFILE_KEY)
if profile is None:
    raise JobContractError("the baseline existing Index profile is not allow-listed")
if (
    profile["source_table"] != BASELINE_CHUNK_TABLE
    or profile["index_name"] != BASELINE_INDEX_NAME
):
    raise JobContractError("the baseline profile does not match the fixed resource pair")

registered = (
    spark.table(VARIANTS_TABLE)
    .where(F.col("project_id") == BASELINE_PROJECT_ID)
    .where(F.col("variant_id") == BASELINE_PHYSICAL_VARIANT_ID)
    .where(F.col("source_table") == BASELINE_CHUNK_TABLE)
    .where(F.col("index_name") == BASELINE_INDEX_NAME)
    .limit(2)
    .count()
)
if registered != 1:
    raise JobContractError("the baseline Index is not registered in Unity Catalog")

baseline_chunk_count = (
    spark.table(BASELINE_CHUNK_TABLE)
    .where(F.col("project_id") == BASELINE_PROJECT_ID)
    .where(F.col("variant_id") == BASELINE_PHYSICAL_VARIANT_ID)
    .count()
)
source_row_count = spark.table(BASELINE_CHUNK_TABLE).count()

index = trigger_index_sync_and_wait(
    WorkspaceClient(),
    index_profile_key=BASELINE_INDEX_PROFILE_KEY,
    index_name=BASELINE_INDEX_NAME,
    source_table=BASELINE_CHUNK_TABLE,
    expected_rows=source_row_count,
)
dbutils.notebook.exit(
    json.dumps(
        {
            "status": "SUCCEEDED",
            "index_name": BASELINE_INDEX_NAME,
            "source_row_count": source_row_count,
            "baseline_chunk_count": baseline_chunk_count,
            "index_status": deep_dict(index.get("status", {})),
        },
        ensure_ascii=False,
    )
)
