-- Run this script in the Databricks workspace selected for the hands-on environment.
-- Idempotent schema-evolution migration for auditable PDF logical deletion.
--
-- MERGE WITH SCHEMA EVOLUTION is used because Databricks SQL ADD COLUMNS has
-- no IF NOT EXISTS form.  The JSON snapshot preserves values on repeat runs
-- while allowing the first run to introduce the new nullable columns.

WITH current_projects AS (
  SELECT *, to_json(struct(*)) AS _row_json
  FROM mikito_toyota_rag_eval.rag_accuracy.toyota_rag_projects
), project_source AS (
  SELECT
    project_id, project_name, description, status, active_variant_id,
    active_dataset_version, default_answer_model_key,
    query_optimizer_model_key, default_judge_model_key, advisor_model_key,
    get_json_object(_row_json, '$.mutation_token') AS mutation_token,
    get_json_object(_row_json, '$.mutation_type') AS mutation_type,
    get_json_object(_row_json, '$.mutation_target_id') AS mutation_target_id,
    CAST(get_json_object(_row_json, '$.mutation_started_at') AS TIMESTAMP)
      AS mutation_started_at,
    created_by, created_at, updated_at
  FROM current_projects
)
MERGE WITH SCHEMA EVOLUTION
INTO mikito_toyota_rag_eval.rag_accuracy.toyota_rag_projects AS target
USING project_source AS source
ON target.project_id=source.project_id
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *;

WITH current_documents AS (
  SELECT *, to_json(struct(*)) AS _row_json
  FROM mikito_toyota_rag_eval.rag_accuracy.toyota_document_registry
), document_source AS (
  SELECT
    document_id, project_id, doc_uri, original_filename, title, summary,
    summary_source, summary_model_key, summary_prompt_version, summary_status,
    page_count, model, model_year, document_type, vehicle_category, category,
    tags, document_date, source, metadata_json, language, sha256,
    source_size_bytes, source_modified_at, uploaded_by, uploaded_at,
    processing_status, processing_message,
    coalesce(get_json_object(_row_json, '$.lifecycle_status'), 'ACTIVE')
      AS lifecycle_status,
    get_json_object(_row_json, '$.deletion_request_id') AS deletion_request_id,
    get_json_object(_row_json, '$.deleted_by') AS deleted_by,
    CAST(get_json_object(_row_json, '$.deleted_at') AS TIMESTAMP) AS deleted_at
  FROM current_documents
)
MERGE WITH SCHEMA EVOLUTION
INTO mikito_toyota_rag_eval.rag_accuracy.toyota_document_registry AS target
USING document_source AS source
ON target.project_id=source.project_id AND target.document_id=source.document_id
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *;

WITH current_variants AS (
  SELECT *, to_json(struct(*)) AS _row_json
  FROM mikito_toyota_rag_eval.rag_accuracy.toyota_index_variants
), variant_source AS (
  SELECT
    variant_id, project_id, source_snapshot_version, parse_schema_version,
    description_element_types, image_output_enabled, chunk_method, chunker,
    chunk_size, parent_chunk_size, parent_context_strategy,
    retrieval_candidate_k, chunk_unit, chunk_overlap, tokenizer_name,
    chunking_model_key, chunker_config_json, cleaning_enabled,
    semantic_metadata_enabled, source_table, index_name, embedding_model_key,
    embedding_endpoint, query_embedding_endpoint, config_hash, code_version,
    from_json(get_json_object(_row_json, '$.source_document_ids'),
      'ARRAY<STRING>') AS source_document_ids,
    coalesce(get_json_object(_row_json, '$.lifecycle_status'), 'READY')
      AS lifecycle_status,
    get_json_object(_row_json, '$.superseded_by_variant_id')
      AS superseded_by_variant_id,
    get_json_object(_row_json, '$.superseded_by_deletion_request_id')
      AS superseded_by_deletion_request_id,
    get_json_object(_row_json, '$.superseded_reason') AS superseded_reason,
    CAST(get_json_object(_row_json, '$.superseded_at') AS TIMESTAMP)
      AS superseded_at,
    created_at
  FROM current_variants
)
MERGE WITH SCHEMA EVOLUTION
INTO mikito_toyota_rag_eval.rag_accuracy.toyota_index_variants AS target
USING variant_source AS source
ON target.project_id=source.project_id AND target.variant_id=source.variant_id
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *;

-- Verification: all pre-existing live records receive an explicit lifecycle.
SELECT
  (SELECT count_if(lifecycle_status IS NULL)
   FROM mikito_toyota_rag_eval.rag_accuracy.toyota_document_registry)
    AS documents_without_lifecycle,
  (SELECT count_if(lifecycle_status IS NULL)
   FROM mikito_toyota_rag_eval.rag_accuracy.toyota_index_variants)
    AS variants_without_lifecycle;
