-- Run this script in the Databricks workspace selected for the hands-on environment.
-- This script is intentionally idempotent.

CREATE CATALOG IF NOT EXISTS mikito_toyota_rag_eval
COMMENT 'Toyota synthetic PDF RAG accuracy evaluation demo';

CREATE SCHEMA IF NOT EXISTS mikito_toyota_rag_eval.rag_accuracy
COMMENT 'Project-scoped RAG preparation, chat, and evaluation resources';

CREATE VOLUME IF NOT EXISTS mikito_toyota_rag_eval.rag_accuracy.documents
COMMENT 'Synthetic demo PDFs and document parsing artifacts';

CREATE TABLE IF NOT EXISTS mikito_toyota_rag_eval.rag_accuracy.toyota_rag_model_catalog (
  model_key STRING NOT NULL,
  display_name STRING NOT NULL,
  target_kind STRING NOT NULL,
  target_name STRING NOT NULL,
  capabilities ARRAY<STRING> NOT NULL,
  endpoint_state STRING,
  embedding_dimension INT,
  max_context_tokens BIGINT,
  max_output_tokens BIGINT,
  region_available BOOLEAN,
  selectable BOOLEAN NOT NULL,
  unavailable_reason STRING,
  verified_at TIMESTAMP NOT NULL
) USING DELTA;

-- Keep UI defaults separate from endpoint discovery.  Discovery may mark a
-- preferred endpoint unavailable, while this policy row remains stable and
-- lets the application choose a deterministic READY fallback.
CREATE TABLE IF NOT EXISTS mikito_toyota_rag_eval.rag_accuracy.toyota_rag_model_defaults (
  capability STRING NOT NULL,
  preferred_model_key STRING NOT NULL,
  fallback_policy STRING NOT NULL,
  rationale STRING,
  updated_at TIMESTAMP NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS mikito_toyota_rag_eval.rag_accuracy.toyota_rag_projects (
  project_id STRING NOT NULL,
  project_name STRING NOT NULL,
  description STRING,
  status STRING NOT NULL,
  active_variant_id STRING,
  active_dataset_version STRING,
  default_answer_model_key STRING,
  query_optimizer_model_key STRING,
  default_judge_model_key STRING,
  advisor_model_key STRING,
  mutation_token STRING,
  mutation_type STRING,
  mutation_target_id STRING,
  mutation_started_at TIMESTAMP,
  created_by STRING NOT NULL,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS mikito_toyota_rag_eval.rag_accuracy.toyota_rag_project_members (
  project_id STRING NOT NULL,
  principal STRING NOT NULL,
  role STRING NOT NULL,
  added_by STRING NOT NULL,
  added_at TIMESTAMP NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS mikito_toyota_rag_eval.rag_accuracy.toyota_document_registry (
  document_id STRING NOT NULL,
  project_id STRING NOT NULL,
  doc_uri STRING NOT NULL,
  original_filename STRING,
  title STRING,
  summary STRING,
  summary_source STRING,
  summary_model_key STRING,
  summary_prompt_version STRING,
  summary_status STRING,
  page_count INT,
  model STRING,
  model_year INT,
  document_type STRING,
  vehicle_category STRING,
  category STRING,
  tags ARRAY<STRING>,
  document_date DATE,
  source STRING,
  metadata_json STRING,
  language STRING,
  sha256 STRING,
  source_size_bytes BIGINT,
  source_modified_at TIMESTAMP,
  uploaded_by STRING,
  uploaded_at TIMESTAMP,
  processing_status STRING,
  processing_message STRING,
  lifecycle_status STRING,
  deletion_request_id STRING,
  deleted_by STRING,
  deleted_at TIMESTAMP
) USING DELTA;

CREATE TABLE IF NOT EXISTS mikito_toyota_rag_eval.rag_accuracy.toyota_vehicle_master (
  model STRING NOT NULL,
  aliases ARRAY<STRING>,
  valid_model_years ARRAY<INT>,
  vehicle_category STRING,
  active BOOLEAN,
  updated_at TIMESTAMP
) USING DELTA;

CREATE TABLE IF NOT EXISTS mikito_toyota_rag_eval.rag_accuracy.toyota_index_variants (
  variant_id STRING NOT NULL,
  project_id STRING NOT NULL,
  source_snapshot_version BIGINT,
  parse_schema_version STRING,
  description_element_types STRING,
  image_output_enabled BOOLEAN,
  chunk_method STRING,
  chunker STRING,
  chunk_size INT,
  parent_chunk_size INT,
  parent_context_strategy STRING,
  retrieval_candidate_k INT,
  chunk_unit STRING,
  chunk_overlap INT,
  tokenizer_name STRING,
  chunking_model_key STRING,
  chunker_config_json STRING,
  cleaning_enabled BOOLEAN,
  semantic_metadata_enabled BOOLEAN,
  source_table STRING,
  index_name STRING,
  embedding_model_key STRING,
  embedding_endpoint STRING,
  query_embedding_endpoint STRING,
  config_hash STRING,
  code_version STRING,
  source_document_ids ARRAY<STRING>,
  lifecycle_status STRING,
  superseded_by_variant_id STRING,
  superseded_by_deletion_request_id STRING,
  superseded_reason STRING,
  superseded_at TIMESTAMP,
  created_at TIMESTAMP
) USING DELTA;

CREATE TABLE IF NOT EXISTS mikito_toyota_rag_eval.rag_accuracy.toyota_rag_chat_sessions (
  session_id STRING NOT NULL,
  project_id STRING NOT NULL,
  title STRING NOT NULL,
  owner_principal STRING NOT NULL,
  status STRING NOT NULL,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS mikito_toyota_rag_eval.rag_accuracy.toyota_rag_chat_messages (
  message_id STRING NOT NULL,
  project_id STRING NOT NULL,
  session_id STRING NOT NULL,
  sequence_no BIGINT NOT NULL,
  role STRING NOT NULL,
  content STRING,
  request_id STRING,
  generation_status STRING,
  config_snapshot_json STRING,
  trace_id STRING,
  citations ARRAY<STRUCT<citation_id: STRING, document_id: STRING, page_numbers: ARRAY<INT>>>,
  created_at TIMESTAMP NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS mikito_toyota_rag_eval.rag_accuracy.toyota_rag_chat_runs (
  request_id STRING NOT NULL,
  project_id STRING NOT NULL,
  session_id STRING NOT NULL,
  status STRING NOT NULL,
  cancel_requested_at TIMESTAMP,
  started_at TIMESTAMP,
  completed_at TIMESTAMP,
  error_message STRING
) USING DELTA;

CREATE TABLE IF NOT EXISTS mikito_toyota_rag_eval.rag_accuracy.toyota_rag_prep_runs (
  prep_run_id STRING NOT NULL,
  project_id STRING NOT NULL,
  run_type STRING NOT NULL,
  document_ids ARRAY<STRING> NOT NULL,
  target_variant_id STRING,
  requested_by STRING NOT NULL,
  status STRING NOT NULL,
  normalized_config_json STRING NOT NULL,
  config_hash STRING NOT NULL,
  job_run_id BIGINT,
  current_step STRING,
  completed_steps INT,
  total_steps INT,
  cancel_requested_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL,
  started_at TIMESTAMP,
  completed_at TIMESTAMP,
  error_message STRING
) USING DELTA;

CREATE TABLE IF NOT EXISTS mikito_toyota_rag_eval.rag_accuracy.toyota_rag_user_preferences (
  principal STRING NOT NULL,
  last_project_id STRING,
  updated_at TIMESTAMP NOT NULL
) USING DELTA;

CREATE TABLE IF NOT EXISTS mikito_toyota_rag_eval.rag_accuracy.toyota_rag_notifications (
  notification_id STRING NOT NULL,
  recipient_principal STRING NOT NULL,
  project_id STRING NOT NULL,
  event_type STRING NOT NULL,
  title STRING NOT NULL,
  message STRING,
  target_path STRING,
  created_at TIMESTAMP NOT NULL,
  read_at TIMESTAMP
) USING DELTA;

CREATE TABLE IF NOT EXISTS mikito_toyota_rag_eval.rag_accuracy.toyota_parsed_v2 (
  project_id STRING NOT NULL,
  document_id STRING NOT NULL,
  doc_uri STRING NOT NULL,
  source_modified_at TIMESTAMP,
  source_size_bytes BIGINT,
  parsed VARIANT,
  parsed_at TIMESTAMP
) USING DELTA;

CREATE TABLE IF NOT EXISTS mikito_toyota_rag_eval.rag_accuracy.toyota_rag_eval_cases (
  project_id STRING NOT NULL,
  eval_case_id STRING NOT NULL,
  question STRING NOT NULL,
  expected_answer STRING,
  expected_facts ARRAY<STRING>,
  relevant_doc_uri STRING,
  relevant_pages ARRAY<INT>,
  relevance_judgments ARRAY<STRUCT<doc_uri: STRING, page_number: INT, relevance_grade: INT>>,
  expected_filter STRUCT<model: STRING, model_year: INT, document_type: STRING, vehicle_category: STRING>,
  model STRING,
  model_year INT,
  question_type STRING,
  is_answerable BOOLEAN,
  language STRING,
  dataset_version STRING,
  dataset_split STRING NOT NULL,
  created_at TIMESTAMP
) USING DELTA;

CREATE TABLE IF NOT EXISTS mikito_toyota_rag_eval.rag_accuracy.toyota_rag_eval_results (
  project_id STRING NOT NULL,
  eval_run_id STRING NOT NULL,
  mlflow_run_id STRING,
  eval_case_id STRING NOT NULL,
  trial_no INT NOT NULL,
  trace_id STRING,
  performance_trace_id STRING,
  dataset_version STRING NOT NULL,
  dataset_split STRING NOT NULL,
  phase_id STRING NOT NULL,
  variant_id STRING NOT NULL,
  config_hash STRING NOT NULL,
  query_type STRING,
  metadata_filtering BOOLEAN,
  reranking BOOLEAN,
  query_optimization BOOLEAN,
  embedding_model_key STRING,
  answer_model_key STRING NOT NULL,
  answer_model_target_kind STRING,
  answer_model_target_name STRING,
  prompt_version STRING,
  validated_filter_json STRING,
  expanded_queries ARRAY<STRING>,
  answer STRING,
  retrieved_items ARRAY<STRUCT<rank: INT, chunk_id: STRING, document_id: STRING, doc_uri: STRING, page_numbers: ARRAY<INT>, matched_child_page_numbers: ARRAY<INT>, parent_page_numbers: ARRAY<INT>, score: DOUBLE>>,
  citations ARRAY<STRUCT<citation_id: STRING, document_id: STRING, doc_uri: STRING, page_numbers: ARRAY<INT>>>,
  retrieval_page_recall_at_10_pages DOUBLE,
  retrieval_page_precision_at_10_pages DOUBLE,
  retrieval_page_dcg_at_10_pages DOUBLE,
  retrieval_page_ndcg_at_10_pages DOUBLE,
  page_coverage_recall_in_top_10_chunks DOUBLE,
  answer_correctness BOOLEAN,
  groundedness BOOLEAN,
  citation_correctness BOOLEAN,
  assessment_rationales MAP<STRING, STRING>,
  filter_exact_match BOOLEAN,
  filter_extraction_ms DOUBLE,
  query_expansion_ms DOUBLE,
  retrieval_ms DOUBLE,
  reranker_ms DOUBLE,
  reranker_status STRING,
  reranker_warnings_json STRING,
  server_ttft_ms DOUBLE,
  client_ttft_ms DOUBLE,
  e2e_latency_ms DOUBLE,
  input_tokens BIGINT,
  output_tokens BIGINT,
  total_tokens BIGINT,
  is_error BOOLEAN,
  error_code STRING,
  evaluated_at TIMESTAMP
) USING DELTA;

CREATE TABLE IF NOT EXISTS mikito_toyota_rag_eval.rag_accuracy.toyota_rag_eval_runs (
  project_id STRING NOT NULL,
  eval_run_id STRING NOT NULL,
  phase_id STRING NOT NULL,
  variant_id STRING NOT NULL,
  dataset_version STRING NOT NULL,
  dataset_split STRING NOT NULL,
  answer_model_key STRING NOT NULL,
  judge_model_key STRING NOT NULL,
  query_optimizer_model_key STRING,
  advisor_model_key STRING,
  trial_count INT NOT NULL,
  requested_by STRING NOT NULL,
  status STRING NOT NULL,
  expected_trials BIGINT,
  completed_trials BIGINT,
  config_json STRING NOT NULL,
  config_hash STRING NOT NULL,
  job_run_id BIGINT,
  created_at TIMESTAMP NOT NULL,
  started_at TIMESTAMP,
  cancel_requested_at TIMESTAMP,
  completed_at TIMESTAMP,
  error_message STRING
) USING DELTA;

CREATE TABLE IF NOT EXISTS mikito_toyota_rag_eval.rag_accuracy.toyota_rag_eval_suggestions (
  suggestion_id STRING NOT NULL,
  project_id STRING NOT NULL,
  eval_run_id STRING NOT NULL,
  phase_id STRING NOT NULL,
  advisor_model_key STRING NOT NULL,
  advisor_prompt_version STRING NOT NULL,
  suggestion_schema_version STRING NOT NULL,
  evidence_json STRING NOT NULL,
  suggestion_json STRING NOT NULL,
  confidence DOUBLE,
  created_at TIMESTAMP NOT NULL,
  accepted_by STRING,
  accepted_at TIMESTAMP
) USING DELTA;

CREATE TABLE IF NOT EXISTS mikito_toyota_rag_eval.rag_accuracy.toyota_chunks_standard_512_v1 (
  chunk_id STRING NOT NULL,
  project_id STRING NOT NULL,
  variant_id STRING NOT NULL,
  document_id STRING NOT NULL,
  chunk_to_retrieve STRING NOT NULL,
  chunk_to_embed STRING NOT NULL,
  doc_uri STRING NOT NULL,
  page_number INT,
  page_numbers ARRAY<INT>,
  title STRING,
  model STRING,
  model_year INT,
  document_type STRING,
  vehicle_category STRING,
  category STRING,
  tags ARRAY<STRING>,
  document_date DATE,
  source STRING,
  metadata_json STRING,
  section_title STRING,
  keywords ARRAY<STRING>,
  parent_chunk_id STRING,
  parent_chunk_to_retrieve STRING,
  parent_page_numbers ARRAY<INT>,
  created_at TIMESTAMP NOT NULL
) USING DELTA
TBLPROPERTIES (delta.enableChangeDataFeed = true);
