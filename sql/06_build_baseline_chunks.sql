-- Run this script in the Databricks workspace selected for the hands-on environment.
-- Build the reproducible Standard / 512 baseline from D01-D08 only.
--
-- Each synthetic PDF page is below the baseline target size, so one physical
-- page becomes one chunk.  ai_parse_document page_id is zero-based, while the UI and
-- qrels use one-based physical page numbers, hence page_number = page_id + 1.
-- Page headers, page footers, and printed page-number elements are removed.
-- Tables, figures, captions, section headings, and their reading order remain.
--
-- Databricks ALTER TABLE ADD COLUMNS has no IF NOT EXISTS form.  The equivalent
-- first-run migration would be:
--   ALTER TABLE mikito_toyota_rag_eval.rag_accuracy.toyota_chunks_standard_512_v1
--   ADD COLUMNS (
--     parent_chunk_to_retrieve STRING,
--     parent_page_numbers ARRAY<INT>
--   )
-- MERGE WITH SCHEMA EVOLUTION below performs that migration safely and is also
-- repeatable when the columns already exist.

-- Delta Sync indexes require Change Data Feed on the source Delta table.
ALTER TABLE mikito_toyota_rag_eval.rag_accuracy.toyota_chunks_standard_512_v1
SET TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');

WITH baseline_documents AS (
  SELECT * FROM VALUES
    ('9ea03add-de22-4c9a-8cac-c8de8e234d6a'),
    ('dffd3261-c8b4-4a85-b1d6-a74576dbdb89'),
    ('00d0cbab-5813-45ea-a4e9-775e9056ad58'),
    ('1a6b0abc-e220-4dd5-85ba-1a6a07baee67'),
    ('031bab44-de2c-47d0-a382-8736da861416'),
    ('36c77b23-77d8-442d-9e1a-d3330e907490'),
    ('247c8253-4187-445b-bb75-1d0180e54830'),
    ('2715b88e-0c3a-4eec-ac4a-f5030b659761')
  AS docs(document_id)
),
parse_gate AS (
  SELECT
    CASE
      WHEN count(*) = 8
       AND count(DISTINCT p.document_id) = 8
       AND count_if(
         coalesce(p.parsed:metadata:version::STRING, '') <> '2.0'
       ) = 0
       AND count_if(
         size(from_json(to_json(p.parsed:document:pages), 'ARRAY<VARIANT>')) <> 5
       ) = 0
       AND count_if(
         coalesce(to_json(p.parsed:error_status), 'null') NOT IN ('null', '[]', '{}')
       ) = 0
      THEN true
      ELSE CAST(
        raise_error(
          'Chunk build aborted: D01-D08 must each have a successful schema 2.0 parse with five pages.'
        ) AS BOOLEAN
      )
    END AS ready
  FROM mikito_toyota_rag_eval.rag_accuracy.toyota_parsed_v2 p
  JOIN baseline_documents d
    ON d.document_id = p.document_id
  WHERE p.project_id = '1f113047-82b3-4327-b2b5-7322ecd26ad9'
),
validated_documents AS (
  SELECT
    p.project_id,
    p.document_id,
    p.doc_uri,
    p.parsed,
    r.title,
    r.summary,
    r.model,
    r.model_year,
    r.document_type,
    r.vehicle_category
  FROM mikito_toyota_rag_eval.rag_accuracy.toyota_parsed_v2 p
  JOIN baseline_documents d
    ON d.document_id = p.document_id
  JOIN mikito_toyota_rag_eval.rag_accuracy.toyota_document_registry r
    ON r.project_id = p.project_id
   AND r.document_id = p.document_id
   AND r.doc_uri = p.doc_uri
  CROSS JOIN parse_gate gate
  WHERE p.project_id = '1f113047-82b3-4327-b2b5-7322ecd26ad9'
    AND gate.ready
),
raw_elements AS (
  SELECT
    d.project_id,
    d.document_id,
    d.doc_uri,
    d.title,
    d.summary,
    d.model,
    d.model_year,
    d.document_type,
    d.vehicle_category,
    element.value:id::INT AS element_id,
    element.value:type::STRING AS element_type,
    element.value:content::STRING AS content,
    element.value:description::STRING AS description,
    element_at(
      transform(
        from_json(
          to_json(element.value:bbox),
          'ARRAY<STRUCT<coord:ARRAY<INT>,page_id:INT>>'
        ),
        box -> box.page_id
      ),
      1
    ) + 1 AS page_number
  FROM validated_documents d,
  LATERAL variant_explode(d.parsed:document:elements) AS element
),
clean_elements AS (
  SELECT
    *,
    regexp_replace(
      trim(coalesce(nullif(trim(content), ''), nullif(trim(description), ''))),
      ' {2,}',
      ' '
    ) AS cleaned_content
  FROM raw_elements
  WHERE element_type NOT IN ('page_header', 'page_footer', 'page_number')
),
formatted_elements AS (
  SELECT
    *,
    CASE
      WHEN element_type = 'title'
        THEN concat('# ', cleaned_content)
      WHEN element_type = 'section_header'
        THEN concat('## ', cleaned_content)
      WHEN element_type = 'table'
        THEN concat('[表]', chr(10), cleaned_content)
      WHEN element_type = 'figure'
        THEN concat(
          '[図]', chr(10), cleaned_content,
          CASE
            WHEN description IS NOT NULL AND trim(description) <> ''
              THEN concat(chr(10), '[図の説明] ', trim(description))
            ELSE ''
          END
        )
      WHEN element_type = 'caption'
        THEN concat('[キャプション] ', cleaned_content)
      WHEN element_type = 'footnote'
        THEN concat('[注記] ', cleaned_content)
      ELSE cleaned_content
    END AS formatted_content
  FROM clean_elements
  WHERE page_number BETWEEN 1 AND 5
    AND cleaned_content IS NOT NULL
    AND cleaned_content <> ''
),
page_element_arrays AS (
  SELECT
    project_id,
    document_id,
    doc_uri,
    title,
    summary,
    model,
    model_year,
    document_type,
    vehicle_category,
    page_number,
    array_sort(
      collect_list(
        named_struct(
          'element_id', element_id,
          'element_type', element_type,
          'section_text', CASE
            WHEN element_type = 'section_header' THEN cleaned_content
            ELSE CAST(NULL AS STRING)
          END,
          'text', formatted_content
        )
      )
    ) AS elements
  FROM formatted_elements
  GROUP BY
    project_id, document_id, doc_uri, title, summary, model, model_year,
    document_type, vehicle_category, page_number
),
page_chunks AS (
  SELECT
    *,
    array_join(
      transform(elements, element -> element.text),
      concat(chr(10), chr(10))
    ) AS page_body,
    nullif(
      array_join(
        transform(
          filter(elements, element -> element.section_text IS NOT NULL),
          element -> element.section_text
        ),
        ' / '
      ),
      ''
    ) AS section_title,
    filter(
      array_distinct(
        concat(
          array(
            model,
            CASE WHEN model_year IS NOT NULL THEN CAST(model_year AS STRING) END,
            document_type,
            vehicle_category
          ),
          transform(
            filter(elements, element -> element.section_text IS NOT NULL),
            element -> element.section_text
          )
        )
      ),
      keyword -> keyword IS NOT NULL AND trim(keyword) <> ''
    ) AS keywords
  FROM page_element_arrays
),
new_chunks AS (
  SELECT
    sha2(
      concat_ws(
        '||',
        'baseline-standard-512-v1',
        project_id,
        document_id,
        CAST(page_number AS STRING)
      ),
      256
    ) AS chunk_id,
    project_id,
    'baseline-standard-512-v1' AS variant_id,
    document_id,
    page_body AS chunk_to_retrieve,
    concat_ws(
      chr(10),
      concat('文書タイトル: ', title),
      CASE WHEN summary IS NOT NULL AND trim(summary) <> ''
        THEN concat('文書概要: ', trim(summary))
      END,
      concat('車種: ', model),
      CASE WHEN model_year IS NOT NULL
        THEN concat('年式: ', CAST(model_year AS STRING))
      END,
      concat('文書種別: ', document_type),
      concat('車両カテゴリ: ', vehicle_category),
      CASE WHEN section_title IS NOT NULL
        THEN concat('セクション: ', section_title)
      END,
      concat('物理ページ: ', CAST(page_number AS STRING)),
      '',
      page_body
    ) AS chunk_to_embed,
    doc_uri,
    page_number,
    array(page_number) AS page_numbers,
    title,
    model,
    model_year,
    document_type,
    vehicle_category,
    section_title,
    keywords,
    CAST(NULL AS STRING) AS parent_chunk_id,
    page_body AS parent_chunk_to_retrieve,
    array(page_number) AS parent_page_numbers
  FROM page_chunks
  WHERE page_body IS NOT NULL AND trim(page_body) <> ''
),
source AS (
  SELECT
    n.chunk_id,
    n.project_id,
    n.variant_id,
    n.document_id,
    n.chunk_to_retrieve,
    n.chunk_to_embed,
    n.doc_uri,
    n.page_number,
    n.page_numbers,
    n.title,
    n.model,
    n.model_year,
    n.document_type,
    n.vehicle_category,
    registry.category,
    registry.tags,
    registry.document_date,
    registry.source,
    registry.metadata_json,
    n.section_title,
    n.keywords,
    n.parent_chunk_id,
    coalesce(existing.created_at, current_timestamp()) AS created_at,
    n.parent_chunk_to_retrieve,
    n.parent_page_numbers
  FROM new_chunks n
  LEFT JOIN mikito_toyota_rag_eval.rag_accuracy.toyota_chunks_standard_512_v1 existing
    ON existing.chunk_id = n.chunk_id
  LEFT JOIN mikito_toyota_rag_eval.rag_accuracy.toyota_document_registry registry
    ON registry.project_id = n.project_id
   AND registry.document_id = n.document_id
)
MERGE WITH SCHEMA EVOLUTION
INTO mikito_toyota_rag_eval.rag_accuracy.toyota_chunks_standard_512_v1 AS target
USING source
ON target.chunk_id = source.chunk_id
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
WHEN NOT MATCHED BY SOURCE
  AND target.project_id = '1f113047-82b3-4327-b2b5-7322ecd26ad9'
  AND target.variant_id = 'baseline-standard-512-v1'
  THEN DELETE;

-- Verification 1: expected 40 rows, eight documents, pages 1-5, no blanks.
SELECT
  count(*) AS chunk_count,
  count(DISTINCT document_id) AS document_count,
  min(page_number) AS min_page_number,
  max(page_number) AS max_page_number,
  count_if(trim(chunk_to_retrieve) = '') AS blank_retrieval_chunks,
  count_if(trim(chunk_to_embed) = '') AS blank_embedding_chunks,
  count_if(parent_chunk_to_retrieve <> chunk_to_retrieve) AS parent_text_mismatches,
  count_if(parent_page_numbers <> page_numbers) AS parent_page_mismatches,
  max(length(chunk_to_retrieve)) AS max_chunk_characters
FROM mikito_toyota_rag_eval.rag_accuracy.toyota_chunks_standard_512_v1
WHERE project_id = '1f113047-82b3-4327-b2b5-7322ecd26ad9'
  AND variant_id = 'baseline-standard-512-v1';

-- Verification 2: chunk_id is the explicit AI Search STRING primary-key field.
-- The index creation request must set primary_key = 'chunk_id'.
SELECT
  count(*) AS duplicate_primary_key_groups,
  coalesce(sum(row_count - 1), 0) AS duplicate_rows
FROM (
  SELECT chunk_id, count(*) AS row_count
  FROM mikito_toyota_rag_eval.rag_accuracy.toyota_chunks_standard_512_v1
  GROUP BY chunk_id
  HAVING count(*) > 1
);

-- Verification 3: every D01-D08 document contributes exactly pages [1,2,3,4,5].
SELECT
  document_id,
  count(*) AS chunk_count,
  array_sort(collect_set(page_number)) AS page_numbers
FROM mikito_toyota_rag_eval.rag_accuracy.toyota_chunks_standard_512_v1
WHERE project_id = '1f113047-82b3-4327-b2b5-7322ecd26ad9'
  AND variant_id = 'baseline-standard-512-v1'
GROUP BY document_id
ORDER BY document_id;

-- Verification 4: STRING / NOT NULL key plus both parent columns are present.
SELECT column_name, data_type, is_nullable
FROM mikito_toyota_rag_eval.information_schema.columns
WHERE table_schema = 'rag_accuracy'
  AND table_name = 'toyota_chunks_standard_512_v1'
  AND column_name IN (
    'chunk_id', 'parent_chunk_to_retrieve', 'parent_page_numbers'
  )
ORDER BY column_name;

-- Verification 5: expected value is true.
SHOW TBLPROPERTIES
  mikito_toyota_rag_eval.rag_accuracy.toyota_chunks_standard_512_v1
  ('delta.enableChangeDataFeed');
