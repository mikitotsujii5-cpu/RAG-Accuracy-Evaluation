# Databricks notebook source
# MAGIC %run ./job_common

# COMMAND ----------

"""Build a logical Variant in a manually created Table/AI Search Index pair.

The App passes only ``prep_run_id``. Project, documents, configuration,
Embedding endpoint, source table, and Index are resolved from the server-side
allow-list. No browser-provided physical name is used and no resource is made.
"""

import json
from datetime import UTC, datetime

from databricks.sdk import WorkspaceClient
from pyspark.sql import functions as F
from pyspark.sql import types as T


CHUNK_SCHEMA = T.StructType(
    [
        T.StructField("chunk_id", T.StringType(), False),
        T.StructField("project_id", T.StringType(), False),
        T.StructField("variant_id", T.StringType(), False),
        T.StructField("document_id", T.StringType(), False),
        T.StructField("chunk_to_retrieve", T.StringType(), False),
        T.StructField("chunk_to_embed", T.StringType(), False),
        T.StructField("doc_uri", T.StringType(), False),
        T.StructField("page_number", T.IntegerType()),
        T.StructField("page_numbers", T.ArrayType(T.IntegerType()), False),
        T.StructField("title", T.StringType()),
        T.StructField("model", T.StringType()),
        T.StructField("model_year", T.IntegerType()),
        T.StructField("document_type", T.StringType()),
        T.StructField("vehicle_category", T.StringType()),
        T.StructField("category", T.StringType()),
        T.StructField("tags", T.ArrayType(T.StringType()), False),
        T.StructField("document_date", T.DateType()),
        T.StructField("source", T.StringType()),
        T.StructField("metadata_json", T.StringType()),
        T.StructField("section_title", T.StringType()),
        T.StructField("keywords", T.ArrayType(T.StringType()), False),
        T.StructField("parent_chunk_id", T.StringType()),
        T.StructField("parent_chunk_to_retrieve", T.StringType()),
        T.StructField("parent_page_numbers", T.ArrayType(T.IntegerType()), False),
        T.StructField("created_at", T.TimestampType(), False),
    ]
)
STARTER_EVAL_SCHEMA = T.StructType(
    [
        T.StructField("project_id", T.StringType(), False),
        T.StructField("eval_case_id", T.StringType(), False),
        T.StructField("question", T.StringType(), False),
        T.StructField("expected_answer", T.StringType()),
        T.StructField("relevant_doc_uri", T.StringType(), False),
        T.StructField("question_type", T.StringType(), False),
        T.StructField("is_answerable", T.BooleanType(), False),
        T.StructField("language", T.StringType(), False),
        T.StructField("dataset_version", T.StringType(), False),
        T.StructField("dataset_split", T.StringType(), False),
        T.StructField("created_at", T.TimestampType(), False),
    ]
)
dbutils.widgets.text("prep_run_id", "", "Preparation run ID")
PREP_RUN_ID = require_uuid(dbutils.widgets.get("prep_run_id").strip(), "prep_run_id")


def update_run(assignments: str) -> None:
    spark.sql(
        f"UPDATE {PREP_RUNS_TABLE} SET {assignments} "
        f"WHERE prep_run_id={sql_string(PREP_RUN_ID)}"
    )


def validate_embedding_model(model_key: str) -> tuple[str, dict[str, object]]:
    rows = (
        spark.table(MODEL_CATALOG_TABLE)
        .where(F.col("model_key") == model_key)
        .limit(2)
        .collect()
    )
    if len(rows) != 1:
        raise JobContractError("Embedding model_key must resolve to exactly one catalog row")
    model = deep_dict(rows[0])
    if (
        model.get("target_kind") != "FMAPI_ENDPOINT"
        or model.get("endpoint_state") != "READY"
        or model.get("selectable") is not True
        or model.get("region_available") is not True
        or "embedding" not in set(model.get("capabilities") or [])
    ):
        raise JobContractError("selected Embedding model is not a READY selectable FMAPI endpoint")
    endpoint = validate_endpoint_name(str(model.get("target_name") or ""))
    return endpoint, model


run_rows = (
    spark.table(PREP_RUNS_TABLE)
    .where(F.col("prep_run_id") == PREP_RUN_ID)
    .limit(2)
    .collect()
)
if len(run_rows) != 1:
    raise JobContractError("prep_run_id must resolve to exactly one persisted row")
run_record = deep_dict(run_rows[0])

if run_record.get("cancel_requested_at") is not None:
    update_run(
        "status='CANCELED', current_step='canceled', completed_at=current_timestamp(), "
        "error_message=NULL"
    )
    dbutils.notebook.exit(json.dumps({"prep_run_id": PREP_RUN_ID, "status": "CANCELED"}))


try:
    request = validate_preparation_record(run_record)
    configuration = request["configuration"]
    activate_on_success = bool(request.get("activate_on_success", True))
    project_id = require_uuid(str(run_record["project_id"]), "project_id")
    document_ids = [
        require_uuid(str(value), "document_id")
        for value in run_record["document_ids"]
    ]
    document_id_sql = ",".join(sql_string(value) for value in sorted(document_ids))
    target_variant_id = require_uuid(
        str(run_record["target_variant_id"]), "target_variant_id"
    )
    config_hash = str(run_record["config_hash"])
    index_profile_key = str(request["index_profile_key"])
    index_profile = resolve_index_profile(index_profile_key, configuration)
    source_table = str(index_profile["source_table"])
    index_name = str(index_profile["index_name"])
    validate_variant_resource_pair(source_table, index_name)
    embedding_model_key = str(configuration["embedding_model_key"])
    embedding_endpoint, _ = validate_embedding_model(embedding_model_key)
    if embedding_endpoint != index_profile["embedding_endpoint"]:
        raise JobContractError(
            "selected Embedding endpoint does not match the existing Index profile"
        )
    method = str(configuration["chunk_method"])
    size = int(configuration["chunk_size_tokens"])
    overlap = OVERLAP_BY_SIZE[size]

    update_run(
        "status='RUNNING', current_step='validation', completed_steps=0, "
        "started_at=coalesce(started_at,current_timestamp()), completed_at=NULL, "
        "error_message=NULL"
    )

    # Every physical prerequisite is checked before generating or writing a
    # chunk. The Job is deliberately unable to create/evolve a Table, create an
    # Index, or repair permissions when the administrator setup is incomplete.
    if not spark.catalog.tableExists(source_table):
        raise JobContractError("allow-listed source Table does not exist")
    existing_fields = [
        (field.name, field.dataType.simpleString())
        for field in spark.table(source_table).schema.fields
    ]
    expected_fields = [
        (field.name, field.dataType.simpleString()) for field in CHUNK_SCHEMA.fields
    ]
    if existing_fields != expected_fields:
        raise JobContractError("allow-listed source Table schema does not match the contract")
    cdf_row = spark.sql(
        f"SHOW TBLPROPERTIES {source_table} ('delta.enableChangeDataFeed')"
    ).first()
    cdf_value = deep_dict(cdf_row or {}).get("value")
    if str(cdf_value or "").lower() != "true":
        raise JobContractError("allow-listed source Table must have Change Data Feed enabled")

    workspace = WorkspaceClient()
    try:
        existing_index = deep_dict(
            workspace.vector_search_indexes.get_index(index_name=index_name)
        )
    except Exception as exc:
        raise JobContractError("allow-listed existing Index is unavailable") from exc
    validate_existing_index(existing_index, profile=index_profile)

    registered_target_rows = (
        spark.table(VARIANTS_TABLE)
        .where(F.col("variant_id") == target_variant_id)
        .limit(2)
        .collect()
    )
    if registered_target_rows:
        if len(registered_target_rows) != 1:
            raise JobContractError("target logical Variant is registered more than once")
        registered_target = deep_dict(registered_target_rows[0])
        if (
            registered_target.get("project_id") != project_id
            or registered_target.get("source_table") != source_table
            or registered_target.get("index_name") != index_name
            or registered_target.get("config_hash") != config_hash
        ):
            raise JobContractError(
                "target logical Variant already exists with another Project or profile"
            )

    project_rows = (
        spark.table(PROJECTS_TABLE)
        .where(F.col("project_id") == project_id)
        .limit(2)
        .collect()
    )
    if len(project_rows) != 1 or deep_dict(project_rows[0]).get("status") == "ARCHIVED":
        raise JobContractError("the preparation Project is missing, duplicated, or archived")

    registry_rows = validate_registry_document_scope(
        project_id=project_id,
        requested_document_ids=document_ids,
        registry_rows=[
        deep_dict(row)
        for row in (
            spark.table(DOCUMENTS_TABLE)
            .where(F.col("project_id") == project_id)
            .where(F.col("document_id").isin(sorted(document_ids)))
            .collect()
        )
        ],
    )
    if any(row.get("processing_status") not in {"PARSED", "READY"} for row in registry_rows):
        raise JobContractError("all requested documents must be PARSED or READY")

    parsed_check = spark.sql(
        f"""
        SELECT count(*) AS row_count, count(DISTINCT document_id) AS document_count,
          count_if(coalesce(parsed:metadata:version::STRING,'')<>'2.0') AS bad_schema,
          count_if(coalesce(size(from_json(to_json(parsed:document:pages),'ARRAY<VARIANT>')),0)<=0) AS bad_pages,
          count_if(coalesce(to_json(parsed:error_status),'null') NOT IN ('null','[]','{{}}')) AS parse_errors
        FROM {PARSED_TABLE}
        WHERE project_id={sql_string(project_id)}
          AND document_id IN ({document_id_sql})
        """
    ).first()
    expected_parse_counts = (len(document_ids), len(document_ids), 0, 0, 0)
    if parsed_check is None or tuple(int(value) for value in parsed_check) != expected_parse_counts:
        raise JobContractError(
            "each requested document must have one successful schema 2.0 parse with pages"
        )
    update_run("current_step='chunking', completed_steps=1")

    element_rows = [
        deep_dict(row)
        for row in spark.sql(
            f"""
            SELECT p.document_id, element.value:id::INT AS element_id,
              element.value:type::STRING AS element_type,
              element.value:content::STRING AS content,
              element.value:description::STRING AS description,
              element_at(transform(from_json(to_json(element.value:bbox),
                'ARRAY<STRUCT<coord:ARRAY<INT>,page_id:INT>>'),box -> box.page_id),1) + 1 AS page_number
            FROM {PARSED_TABLE} p,
            LATERAL variant_explode(p.parsed:document:elements) AS element
            WHERE p.project_id={sql_string(project_id)}
              AND p.document_id IN ({document_id_sql})
            ORDER BY p.document_id, page_number, element_id
            """
        ).collect()
    ]
    elements_by_document: dict[str, list[dict[str, object]]] = {
        document_id: [] for document_id in document_ids
    }
    for element in element_rows:
        elements_by_document[str(element["document_id"])].append(element)

    generated_rows: list[dict[str, object]] = []
    created_at = run_record.get("created_at")
    if not isinstance(created_at, datetime):
        raise JobContractError("preparation run created_at is missing")
    for document in sorted(registry_rows, key=lambda item: str(item["document_id"])):
        document_id = str(document["document_id"])
        chunks = build_document_chunks(
            document,
            elements_by_document[document_id],
            configuration,
            project_id=project_id,
            variant_id=target_variant_id,
        )
        base_keywords = [
            str(value) for value in (
                document.get("model"), document.get("model_year"),
                document.get("document_type"), document.get("vehicle_category"),
                document.get("category"), document.get("document_date"),
                document.get("source"),
            ) if value is not None and str(value).strip()
        ]
        base_keywords.extend(
            str(value) for value in (document.get("tags") or [])
            if value is not None and str(value).strip()
        )
        for chunk in chunks:
            pages = [int(page) for page in chunk["page_numbers"]]
            keywords = list(dict.fromkeys([*base_keywords, *chunk["keywords"]]))
            generated_rows.append(
                {
                    "chunk_id": chunk["chunk_id"], "project_id": project_id,
                    "variant_id": target_variant_id, "document_id": document_id,
                    "chunk_to_retrieve": chunk["chunk_to_retrieve"],
                    "chunk_to_embed": chunk["chunk_to_embed"], "doc_uri": str(document["doc_uri"]),
                    "page_number": min(pages), "page_numbers": pages,
                    "title": document.get("title"), "model": document.get("model"),
                    "model_year": int(document["model_year"]) if document.get("model_year") is not None else None,
                    "document_type": document.get("document_type"),
                    "vehicle_category": document.get("vehicle_category"),
                    "category": document.get("category"),
                    "tags": [str(value) for value in (document.get("tags") or [])],
                    "document_date": document.get("document_date"),
                    "source": document.get("source"),
                    "metadata_json": document.get("metadata_json"),
                    "section_title": chunk["section_title"],
                    "keywords": keywords if configuration["semantic_metadata_enabled"] else [],
                    "parent_chunk_id": chunk["parent_chunk_id"],
                    "parent_chunk_to_retrieve": chunk["parent_chunk_to_retrieve"],
                    "parent_page_numbers": [int(page) for page in chunk["parent_page_numbers"]],
                    "created_at": created_at,
                }
            )
    if not generated_rows or len({row["chunk_id"] for row in generated_rows}) != len(generated_rows):
        raise RuntimeError("chunk generation returned no rows or duplicate primary keys")
    if {row["document_id"] for row in generated_rows} != set(document_ids):
        raise RuntimeError("chunk generation did not cover every requested document")

    chunk_frame = spark.createDataFrame(generated_rows, schema=CHUNK_SCHEMA)
    generated_ids = chunk_frame.select("chunk_id").alias("generated")
    primary_key_collision = (
        spark.table(source_table).alias("existing")
        .join(
            generated_ids,
            on=F.col("existing.chunk_id") == F.col("generated.chunk_id"),
            how="inner",
        )
        .where(
            (F.col("existing.project_id") != project_id)
            | (F.col("existing.variant_id") != target_variant_id)
        )
        .limit(1)
        .count()
    )
    if primary_key_collision:
        raise JobContractError("generated chunk_id collides with another Project or Variant")

    # The source Table was created by an administrator. Replace only this
    # logical Variant slice in one Delta transaction. A separate DELETE then
    # append could leave the existing slice empty when the append fails; the
    # bounded MERGE keeps the previous snapshot intact unless the whole
    # replacement commits. It cannot create or evolve a missing Table.
    chunk_frame.createOrReplaceTempView("_prepared_chunk_source")
    spark.sql(
        f"""
        MERGE INTO {source_table} AS target
        USING _prepared_chunk_source AS source
        ON target.chunk_id=source.chunk_id
        WHEN MATCHED THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *
        WHEN NOT MATCHED BY SOURCE
          AND target.project_id={sql_string(project_id)}
          AND target.variant_id={sql_string(target_variant_id)}
        THEN DELETE
        """
    )
    stored_slice = (
        spark.table(source_table)
        .where(F.col("project_id") == project_id)
        .where(F.col("variant_id") == target_variant_id)
    )
    stored_count = stored_slice.count()
    invalid_stored = (
        stored_slice
        .where(
            F.col("chunk_to_retrieve").isNull()
            | (F.length(F.trim(F.col("chunk_to_retrieve"))) == 0)
            | F.expr("NOT array_contains(parent_page_numbers, page_number)")
        ).limit(1).count()
    )
    if stored_count != len(generated_rows) or invalid_stored:
        raise RuntimeError("stored chunk verification failed")
    total_source_rows = spark.table(source_table).count()
    update_run("current_step='index_sync', completed_steps=2")

    index_status = trigger_index_sync_and_wait(
        workspace,
        index_profile_key=index_profile_key,
        index_name=index_name,
        source_table=source_table,
        expected_rows=total_source_rows,
        trigger_sync=True,
    )
    update_run("current_step='variant_registration', completed_steps=3")

    history = spark.sql(f"DESCRIBE HISTORY {PARSED_TABLE} LIMIT 1").first()
    snapshot_version = int(history["version"]) if history is not None else None
    snapshot_sql = "NULL" if snapshot_version is None else str(snapshot_version)
    parent_size = configuration.get("parent_chunk_size_tokens")
    parent_size_sql = "NULL" if parent_size is None else str(int(parent_size))
    chunker_name = {
        "STANDARD": "deterministic_element_page_packer_v2",
        "SEMANTIC": "deterministic_section_boundary_v2",
        "PARENT_CHILD": "deterministic_parent_child_v2",
    }[method]
    chunker_config = canonical_json(
        {
            "algorithm_version": "2.0", "chunk_method": method,
            "index_profile_key": index_profile_key,
            "target_tokens": size, "overlap_tokens": overlap,
            "parent_tokens": parent_size, "token_estimator": TOKENIZER_NAME,
            "content_profile": configuration["content_profile"],
            "cleaning_enabled": configuration["cleaning_enabled"],
            "semantic_metadata_enabled": configuration["semantic_metadata_enabled"],
            "semantic_boundary_rule": "title_and_section_header" if method == "SEMANTIC" else None,
            "page_boundary_preference_ratio": 0.75, "chunk_count": stored_count,
        }
    )
    spark.sql(
        f"""
        MERGE INTO {VARIANTS_TABLE} AS target
        USING (SELECT {sql_string(target_variant_id)} AS variant_id,
          {sql_string(project_id)} AS project_id,
          CAST({snapshot_sql} AS BIGINT) AS source_snapshot_version,
          '2.0' AS parse_schema_version, '*' AS description_element_types,
          true AS image_output_enabled, {sql_string(method)} AS chunk_method,
          {sql_string(chunker_name)} AS chunker, {size} AS chunk_size,
          CAST({parent_size_sql} AS INT) AS parent_chunk_size,
          {sql_string('RETURN_PARENT' if method == 'PARENT_CHILD' else 'RETURN_SELF')} AS parent_context_strategy,
          50 AS retrieval_candidate_k, 'estimated_token' AS chunk_unit,
          {overlap} AS chunk_overlap, {sql_string(TOKENIZER_NAME)} AS tokenizer_name,
          CAST(NULL AS STRING) AS chunking_model_key,
          {sql_string(chunker_config)} AS chunker_config_json,
          {str(bool(configuration['cleaning_enabled'])).lower()} AS cleaning_enabled,
          {str(bool(configuration['semantic_metadata_enabled'])).lower()} AS semantic_metadata_enabled,
          {sql_string(source_table)} AS source_table, {sql_string(index_name)} AS index_name,
          {sql_string(embedding_model_key)} AS embedding_model_key,
          {sql_string(embedding_endpoint)} AS embedding_endpoint,
          {sql_string(embedding_endpoint)} AS query_embedding_endpoint,
          {sql_string(config_hash)} AS config_hash, 'job-v3' AS code_version,
          {sql_string_array(sorted(document_ids))} AS source_document_ids,
          'READY' AS lifecycle_status) AS source
        ON target.project_id=source.project_id AND target.variant_id=source.variant_id
        WHEN MATCHED THEN UPDATE SET
          target.source_snapshot_version=source.source_snapshot_version,
          target.parse_schema_version=source.parse_schema_version,
          target.description_element_types=source.description_element_types,
          target.image_output_enabled=source.image_output_enabled,
          target.chunk_method=source.chunk_method, target.chunker=source.chunker,
          target.chunk_size=source.chunk_size, target.parent_chunk_size=source.parent_chunk_size,
          target.parent_context_strategy=source.parent_context_strategy,
          target.retrieval_candidate_k=source.retrieval_candidate_k,
          target.chunk_unit=source.chunk_unit, target.chunk_overlap=source.chunk_overlap,
          target.tokenizer_name=source.tokenizer_name,
          target.chunking_model_key=source.chunking_model_key,
          target.chunker_config_json=source.chunker_config_json,
          target.cleaning_enabled=source.cleaning_enabled,
          target.semantic_metadata_enabled=source.semantic_metadata_enabled,
          target.source_table=source.source_table, target.index_name=source.index_name,
          target.embedding_model_key=source.embedding_model_key,
          target.embedding_endpoint=source.embedding_endpoint,
          target.query_embedding_endpoint=source.query_embedding_endpoint,
          target.config_hash=source.config_hash, target.code_version=source.code_version,
          target.source_document_ids=source.source_document_ids,
          target.lifecycle_status=source.lifecycle_status,
          target.superseded_by_variant_id=NULL,
          target.superseded_by_deletion_request_id=NULL,
          target.superseded_reason=NULL, target.superseded_at=NULL
        WHEN NOT MATCHED THEN INSERT (variant_id,project_id,source_snapshot_version,
          parse_schema_version,description_element_types,image_output_enabled,chunk_method,
          chunker,chunk_size,parent_chunk_size,parent_context_strategy,retrieval_candidate_k,
          chunk_unit,chunk_overlap,tokenizer_name,chunking_model_key,chunker_config_json,
          cleaning_enabled,semantic_metadata_enabled,source_table,index_name,
          embedding_model_key,embedding_endpoint,query_embedding_endpoint,config_hash,
          code_version,source_document_ids,lifecycle_status,created_at)
        VALUES (source.variant_id,source.project_id,source.source_snapshot_version,
          source.parse_schema_version,source.description_element_types,
          source.image_output_enabled,source.chunk_method,source.chunker,source.chunk_size,
          source.parent_chunk_size,source.parent_context_strategy,source.retrieval_candidate_k,
          source.chunk_unit,source.chunk_overlap,source.tokenizer_name,source.chunking_model_key,
          source.chunker_config_json,source.cleaning_enabled,source.semantic_metadata_enabled,
          source.source_table,source.index_name,source.embedding_model_key,
          source.embedding_endpoint,source.query_embedding_endpoint,source.config_hash,
          source.code_version,source.source_document_ids,source.lifecycle_status,
          current_timestamp())
        """
    )
    variant_rows = (
        spark.table(VARIANTS_TABLE).where(F.col("project_id") == project_id)
        .where(F.col("variant_id") == target_variant_id)
        .where(F.col("source_table") == source_table).where(F.col("index_name") == index_name)
        .count()
    )
    if variant_rows != 1:
        raise RuntimeError("Variant registration verification failed")
    update_run("current_step='starter_evaluation_seed', completed_steps=3")
    existing_cases = [
        deep_dict(row)
        for row in (
            spark.table(EVAL_CASES_TABLE)
            .where(F.col("project_id") == project_id)
            .where(
                (
                    (F.col("dataset_version") == STARTER_DATASET_VERSION)
                    & (F.col("dataset_split") == STARTER_DATASET_SPLIT)
                )
                | F.col("eval_case_id").isin(list(STARTER_EVAL_CASE_IDS))
            )
            .select(
                "eval_case_id", "question", "question_type",
                "dataset_version", "dataset_split",
            )
            .collect()
        )
    ]
    starter_rows = build_starter_evaluation_cases(
        project_id=project_id,
        registry_rows=registry_rows,
        existing_cases=existing_cases,
        created_at=created_at,
    )
    if starter_rows:
        starter_frame = spark.createDataFrame(starter_rows, schema=STARTER_EVAL_SCHEMA)
        starter_frame.createOrReplaceTempView("_starter_eval_case_source")
        spark.sql(
            f"""
            MERGE INTO {EVAL_CASES_TABLE} AS target
            USING _starter_eval_case_source AS source
            ON target.project_id=source.project_id
             AND target.eval_case_id=source.eval_case_id
            WHEN NOT MATCHED THEN INSERT (
              project_id, eval_case_id, question, expected_answer, expected_facts,
              relevant_doc_uri, relevant_pages, relevance_judgments,
              expected_filter, model, model_year, question_type, is_answerable,
              language, dataset_version, dataset_split, created_at
            ) VALUES (
              source.project_id, source.eval_case_id, source.question,
              source.expected_answer, CAST(array() AS ARRAY<STRING>),
              source.relevant_doc_uri, CAST(array() AS ARRAY<INT>),
              CAST(array() AS ARRAY<STRUCT<doc_uri:STRING,page_number:INT,relevance_grade:INT>>),
              CAST(NULL AS STRUCT<model:STRING,model_year:INT,document_type:STRING,vehicle_category:STRING>),
              CAST(NULL AS STRING), CAST(NULL AS INT), source.question_type,
              source.is_answerable, source.language, source.dataset_version,
              source.dataset_split, source.created_at
            )
            """
        )
    starter_case_count = (
        spark.table(EVAL_CASES_TABLE)
        .where(F.col("project_id") == project_id)
        .where(F.col("dataset_version") == STARTER_DATASET_VERSION)
        .where(F.col("dataset_split") == STARTER_DATASET_SPLIT)
        .count()
    )
    if starter_case_count < 3:
        raise RuntimeError("three starter evaluation questions were not persisted")
    spark.sql(
        f"""UPDATE {DOCUMENTS_TABLE} SET processing_status='READY',
        processing_message='チャンク作成とAI Search同期が完了しました'
        WHERE project_id={sql_string(project_id)}
          AND document_id IN ({document_id_sql})
          AND coalesce(lifecycle_status, 'ACTIVE')='ACTIVE'"""
    )
    if activate_on_success:
        spark.sql(
            f"""UPDATE {PROJECTS_TABLE} SET status='ACTIVE',
            active_variant_id={sql_string(target_variant_id)},
            active_dataset_version=coalesce(active_dataset_version, {sql_string(STARTER_DATASET_VERSION)}),
            updated_at=current_timestamp()
            WHERE project_id={sql_string(project_id)}
              AND status NOT IN ('ARCHIVED', 'DELETING')"""
        )
    update_run(
        "status='SUCCEEDED', current_step='completed', completed_steps=4, "
        "completed_at=current_timestamp(), error_message=NULL"
    )
    success_payload = {
        "prep_run_id": PREP_RUN_ID, "status": "SUCCEEDED",
        "project_id": project_id,
        "variant_id": target_variant_id, "chunk_method": method,
        "chunk_size": size, "chunk_count": stored_count,
        "index_profile_key": index_profile_key,
        "starter_evaluation_case_count": starter_case_count,
        "source_table": source_table, "index_name": index_name,
        "activate_on_success": activate_on_success,
        "index_ready": True, "index_status": deep_dict(index_status.get("status", {})),
    }
except Exception as exc:
    print("data preparation failed", type(exc).__name__, str(exc)[:2000])
    # Deleting a PDF clears an affected active Variant and marks the Project
    # REBUILDING while this activating successor runs.  If that successor
    # fails, do not leave the Project indefinitely waiting for an index that
    # can never become READY.  The predicate is a compare-and-set: it cannot
    # overwrite a newer successful build that has already installed an active
    # Variant, and non-activating successors never change Project state.
    if bool(locals().get("activate_on_success", False)):
        try:
            spark.sql(
                f"""UPDATE {PROJECTS_TABLE}
                SET status='NEEDS_BUILD', updated_at=current_timestamp()
                WHERE project_id={sql_string(str(locals().get('project_id', '')))}
                  AND status='REBUILDING' AND active_variant_id IS NULL"""
            )
        except Exception as project_state_exc:
            print(
                "failed to reconcile Project after preparation failure",
                type(project_state_exc).__name__,
                str(project_state_exc)[:1000],
            )
    public_error = (
        str(exc)[:900] if isinstance(exc, JobContractError)
        else "データ準備Jobに失敗しました。Lakeflow Jobの実行ログを確認してください。"
    )
    update_run(
        "status='FAILED', current_step='failed', completed_at=current_timestamp(), "
        f"error_message={sql_string(public_error)}"
    )
    raise

dbutils.notebook.exit(json.dumps(success_payload, ensure_ascii=False))
