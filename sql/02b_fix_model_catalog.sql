-- Remove the one duplicate key produced during the first discovery sync.
-- The canonical emb-qwen3-0-6b row is retained and is the key referenced by
-- the baseline Variant.  This statement is exact-targeted and idempotent.

DELETE FROM mikito_toyota_rag_eval.rag_accuracy.toyota_rag_model_catalog
WHERE model_key = 'emb-qwen3-embedding-0-6b'
  AND target_name = 'databricks-qwen3-embedding-0-6b';

SELECT
  count(*) AS embedding_rows,
  count(DISTINCT target_name) AS embedding_targets
FROM mikito_toyota_rag_eval.rag_accuracy.toyota_rag_model_catalog
WHERE selectable = TRUE
  AND endpoint_state = 'READY'
  AND array_contains(capabilities, 'embedding');
