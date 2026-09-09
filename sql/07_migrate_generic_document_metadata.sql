-- Run this script in the Databricks workspace selected for the hands-on environment.
-- Idempotent registry migration from the Toyota-specific upload contract to
-- the generic RAG document metadata contract.
--
-- Delta's MERGE WITH SCHEMA EVOLUTION adds the five new columns on the first
-- run.  ``to_json(struct(*))`` lets the same statement preserve those columns
-- on later runs without referring to a column that may not exist yet.

WITH current_registry AS (
  SELECT
    *,
    to_json(struct(*)) AS _row_json,
    substring(
      coalesce(
        nullif(trim(title), ''),
        nullif(
          trim(regexp_replace(regexp_replace(original_filename, '(?i)\\.pdf$', ''), '_+', ' ')),
          ''
        ),
        '文書'
      ),
      1,
      300
    ) AS _resolved_title
  FROM rag_accuracy_demo.rag_accuracy.toyota_document_registry
),
migration_source AS (
  SELECT
    document_id,
    project_id,
    doc_uri,
    original_filename,
    _resolved_title AS title,
    summary,
    summary_source,
    summary_model_key,
    summary_prompt_version,
    summary_status,
    page_count,
    model,
    model_year,
    document_type,
    vehicle_category,
    coalesce(get_json_object(_row_json, '$.category'), document_type) AS category,
    coalesce(
      from_json(get_json_object(_row_json, '$.tags'), 'ARRAY<STRING>'),
      CAST(array() AS ARRAY<STRING>)
    ) AS tags,
    CAST(get_json_object(_row_json, '$.document_date') AS DATE) AS document_date,
    get_json_object(_row_json, '$.source') AS source,
    coalesce(
      get_json_object(_row_json, '$.metadata_json'),
      to_json(named_struct(
        'schema_version', '1.0',
        'common', named_struct(
          'title', _resolved_title,
          'category', document_type,
          'tags', CAST(array() AS ARRAY<STRING>),
          'document_date', CAST(NULL AS STRING),
          'source', CAST(NULL AS STRING)
        ),
        'custom', map_from_arrays(
          CAST(array() AS ARRAY<STRING>), CAST(array() AS ARRAY<STRING>)
        ),
        'legacy', named_struct(
          'model', model,
          'model_year', model_year,
          'document_type', document_type,
          'vehicle_category', vehicle_category
        )
      ))
    ) AS metadata_json,
    language,
    sha256,
    source_size_bytes,
    source_modified_at,
    uploaded_by,
    uploaded_at,
    processing_status,
    processing_message
  FROM current_registry
)
MERGE WITH SCHEMA EVOLUTION
INTO rag_accuracy_demo.rag_accuracy.toyota_document_registry AS target
USING migration_source AS source
ON target.project_id = source.project_id
 AND target.document_id = source.document_id
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *;

-- Safe verification: these counts should all be zero after migration.
SELECT
  count_if(title IS NULL OR trim(title) = '') AS missing_titles,
  count_if(tags IS NULL) AS null_tag_arrays,
  count_if(
    metadata_json IS NULL
    OR get_json_object(metadata_json, '$.schema_version') IS NULL
  ) AS invalid_metadata_json,
  count_if(get_json_object(metadata_json, '$.schema_version') <> '1.0') AS bad_schema_version
FROM rag_accuracy_demo.rag_accuracy.toyota_document_registry;
