-- Idempotent baseline Project, model, vehicle, and Variant seed data.

MERGE INTO mikito_toyota_rag_eval.rag_accuracy.toyota_rag_model_catalog AS target
USING (
  SELECT * FROM VALUES
    ('emb-qwen3-0-6b', 'Qwen3 Embedding 0.6B', 'FMAPI_ENDPOINT', 'databricks-qwen3-embedding-0-6b', array('embedding'), 'READY', NULL, NULL, NULL, true, true, NULL),
    ('emb-gte-large-en', 'GTE Large English', 'FMAPI_ENDPOINT', 'databricks-gte-large-en', array('embedding'), 'READY', 1024, NULL, NULL, true, true, NULL),
    ('emb-bge-large-en', 'BGE Large English', 'FMAPI_ENDPOINT', 'databricks-bge-large-en', array('embedding'), 'READY', 1024, NULL, NULL, true, true, NULL),
    ('llm-gpt-5-6-luna', 'GPT-5.6 Luna', 'FMAPI_ENDPOINT', 'databricks-gpt-5-6-luna', array('chat', 'streaming', 'tool_calling'), 'READY', NULL, NULL, NULL, true, true, NULL),
    ('llm-gpt-5-6-terra', 'GPT-5.6 Terra', 'FMAPI_ENDPOINT', 'databricks-gpt-5-6-terra', array('chat', 'streaming', 'tool_calling', 'judge', 'advisor'), 'READY', NULL, NULL, NULL, true, true, NULL)
  AS source(
    model_key, display_name, target_kind, target_name, capabilities,
    endpoint_state, embedding_dimension, max_context_tokens, max_output_tokens,
    region_available, selectable, unavailable_reason
  )
) AS source
ON target.model_key = source.model_key
WHEN MATCHED THEN UPDATE SET
  target.display_name = source.display_name,
  target.target_kind = source.target_kind,
  target.target_name = source.target_name,
  target.capabilities = source.capabilities,
  target.endpoint_state = source.endpoint_state,
  target.embedding_dimension = source.embedding_dimension,
  target.max_context_tokens = source.max_context_tokens,
  target.max_output_tokens = source.max_output_tokens,
  target.region_available = source.region_available,
  target.selectable = source.selectable,
  target.unavailable_reason = source.unavailable_reason,
  target.verified_at = current_timestamp()
WHEN NOT MATCHED THEN INSERT (
  model_key, display_name, target_kind, target_name, capabilities,
  endpoint_state, embedding_dimension, max_context_tokens, max_output_tokens,
  region_available, selectable, unavailable_reason, verified_at
) VALUES (
  source.model_key, source.display_name, source.target_kind, source.target_name,
  source.capabilities, source.endpoint_state, source.embedding_dimension,
  source.max_context_tokens, source.max_output_tokens, source.region_available,
  source.selectable, source.unavailable_reason, current_timestamp()
);

-- Qwen3 Embedding 0.6B is the default because the configured FMAPI endpoint
-- is multilingual and supports Japanese retrieval.  The
-- application must use the fallback policy only when this row is not READY,
-- selectable, and region-available in the model catalog.
MERGE INTO mikito_toyota_rag_eval.rag_accuracy.toyota_rag_model_defaults AS target
USING (
  SELECT
    'embedding' AS capability,
    'emb-qwen3-0-6b' AS preferred_model_key,
    'READY_SELECTABLE_DISPLAY_NAME_MODEL_KEY_ASC' AS fallback_policy,
    'READYを確認した多言語Embedding。日本語PDFの既定値。' AS rationale
) AS source
ON target.capability = source.capability
WHEN MATCHED THEN UPDATE SET
  target.preferred_model_key = source.preferred_model_key,
  target.fallback_policy = source.fallback_policy,
  target.rationale = source.rationale,
  target.updated_at = current_timestamp()
WHEN NOT MATCHED THEN INSERT (
  capability, preferred_model_key, fallback_policy, rationale, updated_at
) VALUES (
  source.capability, source.preferred_model_key, source.fallback_policy,
  source.rationale, current_timestamp()
);

MERGE INTO mikito_toyota_rag_eval.rag_accuracy.toyota_rag_projects AS target
USING (
  SELECT * FROM VALUES
    ('1f113047-82b3-4327-b2b5-7322ecd26ad9', 'Toyota RAG Baseline', 'D01-D08を固定コーパスとしてPhase 1-5を比較するプロジェクト', 'ACTIVE', 'baseline-standard-512-v1', 'v1.0.0', 'llm-gpt-5-6-luna', 'llm-gpt-5-6-luna', 'llm-gpt-5-6-terra', 'llm-gpt-5-6-terra'),
    ('ba8848ed-845b-49ed-9cd3-42aef3c0ad60', 'Toyota Scan Parsing', 'D09の画像PDFをD05と分離して解析比較するプロジェクト', 'ACTIVE', NULL, NULL, 'llm-gpt-5-6-luna', 'llm-gpt-5-6-luna', 'llm-gpt-5-6-terra', 'llm-gpt-5-6-terra')
  AS source(
    project_id, project_name, description, status, active_variant_id,
    active_dataset_version, default_answer_model_key, query_optimizer_model_key,
    default_judge_model_key, advisor_model_key
  )
) AS source
ON target.project_id = source.project_id
WHEN MATCHED THEN UPDATE SET
  target.project_name = source.project_name,
  target.description = source.description,
  target.status = source.status,
  target.active_variant_id = source.active_variant_id,
  target.active_dataset_version = source.active_dataset_version,
  target.default_answer_model_key = source.default_answer_model_key,
  target.query_optimizer_model_key = source.query_optimizer_model_key,
  target.default_judge_model_key = source.default_judge_model_key,
  target.advisor_model_key = source.advisor_model_key,
  target.updated_at = current_timestamp()
WHEN NOT MATCHED THEN INSERT (
  project_id, project_name, description, status, active_variant_id,
  active_dataset_version, default_answer_model_key, query_optimizer_model_key,
  default_judge_model_key, advisor_model_key, created_by, created_at, updated_at
) VALUES (
  source.project_id, source.project_name, source.description, source.status,
  source.active_variant_id, source.active_dataset_version,
  source.default_answer_model_key, source.query_optimizer_model_key,
  source.default_judge_model_key, source.advisor_model_key,
  current_user(), current_timestamp(), current_timestamp()
);

MERGE INTO mikito_toyota_rag_eval.rag_accuracy.toyota_rag_project_members AS target
USING (
  SELECT project_id, current_user() AS principal, 'OWNER' AS role
  FROM VALUES
    ('1f113047-82b3-4327-b2b5-7322ecd26ad9'),
    ('ba8848ed-845b-49ed-9cd3-42aef3c0ad60')
  AS projects(project_id)
) AS source
ON target.project_id = source.project_id AND target.principal = source.principal
WHEN MATCHED THEN UPDATE SET
  target.role = source.role,
  target.added_by = current_user(),
  target.added_at = current_timestamp()
WHEN NOT MATCHED THEN INSERT (
  project_id, principal, role, added_by, added_at
) VALUES (
  source.project_id, source.principal, source.role,
  current_user(), current_timestamp()
);

MERGE INTO mikito_toyota_rag_eval.rag_accuracy.toyota_vehicle_master AS target
USING (
  SELECT * FROM VALUES
    ('Prius', array('プリウス', 'PRIUS'), array(2023, 2024), 'passenger_car', true),
    ('Crown Sport', array('クラウンスポーツ', 'CROWN SPORT'), array(2024), 'suv', true),
    ('Common', array('共通', 'Toyota'), CAST(array() AS ARRAY<INT>), 'all', true)
  AS source(model, aliases, valid_model_years, vehicle_category, active)
) AS source
ON target.model = source.model
WHEN MATCHED THEN UPDATE SET
  target.aliases = source.aliases,
  target.valid_model_years = source.valid_model_years,
  target.vehicle_category = source.vehicle_category,
  target.active = source.active,
  target.updated_at = current_timestamp()
WHEN NOT MATCHED THEN INSERT (
  model, aliases, valid_model_years, vehicle_category, active, updated_at
) VALUES (
  source.model, source.aliases, source.valid_model_years,
  source.vehicle_category, source.active, current_timestamp()
);

MERGE INTO mikito_toyota_rag_eval.rag_accuracy.toyota_index_variants AS target
USING (
  SELECT
    'baseline-standard-512-v1' AS variant_id,
    '1f113047-82b3-4327-b2b5-7322ecd26ad9' AS project_id,
    CAST(NULL AS BIGINT) AS source_snapshot_version,
    '2.0' AS parse_schema_version,
    '*' AS description_element_types,
    true AS image_output_enabled,
    'STANDARD' AS chunk_method,
    'page_aware_standard_v1' AS chunker,
    512 AS chunk_size,
    CAST(NULL AS INT) AS parent_chunk_size,
    CAST(NULL AS STRING) AS parent_context_strategy,
    50 AS retrieval_candidate_k,
    'token_target' AS chunk_unit,
    64 AS chunk_overlap,
    'approx_page_aware' AS tokenizer_name,
    CAST(NULL AS STRING) AS chunking_model_key,
    '{"page_aligned":true,"max_target_tokens":512,"note":"Demo pages are below the target size"}' AS chunker_config_json,
    true AS cleaning_enabled,
    true AS semantic_metadata_enabled,
    'mikito_toyota_rag_eval.rag_accuracy.toyota_chunks_standard_512_v1' AS source_table,
    'mikito_toyota_rag_eval.rag_accuracy.toyota_chunks_standard_512_v1_index' AS index_name,
    'emb-qwen3-0-6b' AS embedding_model_key,
    'databricks-qwen3-embedding-0-6b' AS embedding_endpoint,
    'databricks-qwen3-embedding-0-6b' AS query_embedding_endpoint,
    '2c6ef4b9931ae7b8cd2851ba0b5e41acac6d81b2a216355c9d05e5f791cf630e' AS config_hash,
    'v1' AS code_version
) AS source
ON target.project_id = source.project_id AND target.variant_id = source.variant_id
WHEN MATCHED THEN UPDATE SET
  target.source_snapshot_version = source.source_snapshot_version,
  target.parse_schema_version = source.parse_schema_version,
  target.description_element_types = source.description_element_types,
  target.image_output_enabled = source.image_output_enabled,
  target.chunk_method = source.chunk_method,
  target.chunker = source.chunker,
  target.chunk_size = source.chunk_size,
  target.parent_chunk_size = source.parent_chunk_size,
  target.parent_context_strategy = source.parent_context_strategy,
  target.retrieval_candidate_k = source.retrieval_candidate_k,
  target.chunk_unit = source.chunk_unit,
  target.chunk_overlap = source.chunk_overlap,
  target.tokenizer_name = source.tokenizer_name,
  target.chunking_model_key = source.chunking_model_key,
  target.chunker_config_json = source.chunker_config_json,
  target.cleaning_enabled = source.cleaning_enabled,
  target.semantic_metadata_enabled = source.semantic_metadata_enabled,
  target.source_table = source.source_table,
  target.index_name = source.index_name,
  target.embedding_model_key = source.embedding_model_key,
  target.embedding_endpoint = source.embedding_endpoint,
  target.query_embedding_endpoint = source.query_embedding_endpoint,
  target.config_hash = source.config_hash,
  target.code_version = source.code_version
WHEN NOT MATCHED THEN INSERT (
  variant_id, project_id, source_snapshot_version, parse_schema_version,
  description_element_types, image_output_enabled, chunk_method, chunker,
  chunk_size, parent_chunk_size, parent_context_strategy, retrieval_candidate_k,
  chunk_unit, chunk_overlap, tokenizer_name, chunking_model_key,
  chunker_config_json, cleaning_enabled, semantic_metadata_enabled, source_table,
  index_name, embedding_model_key, embedding_endpoint, query_embedding_endpoint,
  config_hash, code_version, created_at
) VALUES (
  source.variant_id, source.project_id, source.source_snapshot_version,
  source.parse_schema_version, source.description_element_types,
  source.image_output_enabled, source.chunk_method, source.chunker,
  source.chunk_size, source.parent_chunk_size, source.parent_context_strategy,
  source.retrieval_candidate_k, source.chunk_unit, source.chunk_overlap,
  source.tokenizer_name, source.chunking_model_key, source.chunker_config_json,
  source.cleaning_enabled, source.semantic_metadata_enabled, source.source_table,
  source.index_name, source.embedding_model_key, source.embedding_endpoint,
  source.query_embedding_endpoint, source.config_hash, source.code_version,
  current_timestamp()
);

-- Verification: one deterministic embedding default.  If Qwen is not READY
-- after endpoint discovery, the first READY/selectable display_name/model_key
-- pair is returned instead.
SELECT
  catalog.model_key AS resolved_default_model_key,
  catalog.display_name AS resolved_default_display_name,
  catalog.target_name AS resolved_default_endpoint,
  catalog.model_key = defaults.preferred_model_key AS preferred_is_available
FROM mikito_toyota_rag_eval.rag_accuracy.toyota_rag_model_catalog catalog
CROSS JOIN mikito_toyota_rag_eval.rag_accuracy.toyota_rag_model_defaults defaults
WHERE defaults.capability = 'embedding'
  AND catalog.target_kind = 'FMAPI_ENDPOINT'
  AND catalog.endpoint_state = 'READY'
  AND catalog.selectable = TRUE
  AND catalog.region_available = TRUE
  AND array_contains(catalog.capabilities, 'embedding')
ORDER BY
  (catalog.model_key = defaults.preferred_model_key) DESC,
  lower(catalog.display_name),
  catalog.model_key
LIMIT 1;
