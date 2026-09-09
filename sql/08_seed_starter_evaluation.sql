-- Run this script in the Databricks workspace selected for the hands-on environment.
-- Backfill three Project-scoped starter questions for existing Projects.
--
-- These rows intentionally do not invent an expected answer or page label.
-- The document URI is known, while expected_answer, relevant_pages, and qrels
-- remain empty.  Consequently answer-correctness and page-level retrieval
-- metrics are NULL until a user creates a fully labelled evaluation case.

WITH ranked_documents AS (
  SELECT
    project_id,
    document_id,
    doc_uri,
    coalesce(
      nullif(trim(title), ''),
      nullif(trim(original_filename), ''),
      'PDF'
    ) AS document_title,
    row_number() OVER (
      PARTITION BY project_id ORDER BY document_id, doc_uri
    ) AS document_rank,
    count(*) OVER (PARTITION BY project_id) AS document_count
  FROM rag_accuracy_demo.rag_accuracy.toyota_document_registry
  WHERE processing_status IN ('PARSED', 'READY')
    AND doc_uri IS NOT NULL
    AND trim(doc_uri) <> ''
),
project_counts AS (
  SELECT DISTINCT project_id, document_count
  FROM ranked_documents
),
slots AS (
  SELECT slot_number
  FROM VALUES (1), (2), (3) AS values(slot_number)
),
selected_documents AS (
  SELECT
    p.project_id,
    s.slot_number,
    d.doc_uri,
    d.document_title
  FROM project_counts p
  CROSS JOIN slots s
  INNER JOIN ranked_documents d
    ON d.project_id = p.project_id
   AND d.document_rank = pmod(s.slot_number - 1, p.document_count) + 1
),
candidate_source AS (
  SELECT
    project_id,
    concat('starter-sample-', lpad(CAST(slot_number AS STRING), 2, '0')) AS eval_case_id,
    CASE slot_number
      WHEN 1 THEN concat('「', substring(document_title, 1, 300), '」の概要を教えてください。')
      WHEN 2 THEN concat('「', substring(document_title, 1, 300), '」の重要なポイントを3つ教えてください。')
      ELSE concat('「', substring(document_title, 1, 300), '」に記載された主な手順や条件を教えてください。')
    END AS question,
    doc_uri AS relevant_doc_uri,
    slot_number
  FROM selected_documents
),
source AS (
  SELECT candidate.*
  FROM candidate_source candidate
  LEFT ANTI JOIN rag_accuracy_demo.rag_accuracy.toyota_rag_eval_cases existing
    ON existing.project_id = candidate.project_id
   AND existing.dataset_version = 'starter-v1'
   AND existing.dataset_split = 'development'
   AND lower(trim(existing.question)) = lower(trim(candidate.question))
   AND existing.eval_case_id <> candidate.eval_case_id
)
MERGE INTO rag_accuracy_demo.rag_accuracy.toyota_rag_eval_cases AS target
USING source
ON target.project_id = source.project_id
AND target.eval_case_id = source.eval_case_id
WHEN MATCHED AND target.dataset_version = 'starter-v1'
  AND target.dataset_split = 'development'
  AND target.question_type = 'starter_sample_unlabeled'
THEN UPDATE SET
  target.question = source.question,
  target.expected_answer = NULL,
  target.expected_facts = CAST(array() AS ARRAY<STRING>),
  target.relevant_doc_uri = source.relevant_doc_uri,
  target.relevant_pages = CAST(array() AS ARRAY<INT>),
  target.relevance_judgments = CAST(array() AS ARRAY<STRUCT<doc_uri:STRING,page_number:INT,relevance_grade:INT>>),
  target.expected_filter = NULL,
  target.model = NULL,
  target.model_year = NULL,
  target.is_answerable = TRUE,
  target.language = 'ja'
WHEN NOT MATCHED THEN INSERT (
  project_id, eval_case_id, question, expected_answer, expected_facts,
  relevant_doc_uri, relevant_pages, relevance_judgments, expected_filter,
  model, model_year, question_type, is_answerable, language,
  dataset_version, dataset_split, created_at
) VALUES (
  source.project_id, source.eval_case_id, source.question, NULL,
  CAST(array() AS ARRAY<STRING>), source.relevant_doc_uri,
  CAST(array() AS ARRAY<INT>),
  CAST(array() AS ARRAY<STRUCT<doc_uri:STRING,page_number:INT,relevance_grade:INT>>),
  CAST(NULL AS STRUCT<model:STRING,model_year:INT,document_type:STRING,vehicle_category:STRING>),
  NULL, NULL, 'starter_sample_unlabeled', TRUE, 'ja',
  'starter-v1', 'development', current_timestamp()
);

-- Select the starter dataset only when a Project does not already have an
-- explicitly selected, human-labelled evaluation dataset.
UPDATE rag_accuracy_demo.rag_accuracy.toyota_rag_projects AS project
SET active_dataset_version = 'starter-v1', updated_at = current_timestamp()
WHERE active_dataset_version IS NULL
  AND EXISTS (
    SELECT 1
    FROM rag_accuracy_demo.rag_accuracy.toyota_rag_eval_cases eval_case
    WHERE eval_case.project_id = project.project_id
      AND eval_case.dataset_version = 'starter-v1'
      AND eval_case.dataset_split = 'development'
  );

-- Expected after a clean run: every Project with a parsed/ready document has
-- three starter questions, no fabricated page labels, and no fabricated
-- expected answers.
SELECT
  project_id,
  count(*) AS starter_case_count,
  count_if(expected_answer IS NOT NULL OR size(expected_facts) > 0) AS fabricated_answer_labels,
  count_if(size(relevant_pages) > 0 OR size(relevance_judgments) > 0) AS fabricated_page_labels
FROM rag_accuracy_demo.rag_accuracy.toyota_rag_eval_cases
WHERE dataset_version = 'starter-v1'
  AND dataset_split = 'development'
  AND question_type = 'starter_sample_unlabeled'
GROUP BY project_id
ORDER BY project_id;
