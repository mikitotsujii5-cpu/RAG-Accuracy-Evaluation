-- Run this script in the Databricks workspace selected for the hands-on environment.
-- Seed the fixed v1.0.0 evaluation set for the baseline Project.
--
-- The checked-in JSONL uses the human-readable D01-D08 codes.  This script
-- resolves those codes through their immutable document_id values and the
-- document registry, so every qrel stores the real Unity Catalog Volume URI.
-- Re-running the script updates the same (project_id, eval_case_id) rows.

WITH document_ids AS (
  SELECT * FROM VALUES
    ('D01', '9ea03add-de22-4c9a-8cac-c8de8e234d6a'),
    ('D02', 'dffd3261-c8b4-4a85-b1d6-a74576dbdb89'),
    ('D03', '00d0cbab-5813-45ea-a4e9-775e9056ad58'),
    ('D04', '1a6b0abc-e220-4dd5-85ba-1a6a07baee67'),
    ('D05', '031bab44-de2c-47d0-a382-8736da861416'),
    ('D06', '36c77b23-77d8-442d-9e1a-d3330e907490'),
    ('D07', '247c8253-4187-445b-bb75-1d0180e54830'),
    ('D08', '2715b88e-0c3a-4eec-ac4a-f5030b659761')
  AS docs(doc_code, document_id)
),
registry_map AS (
  SELECT
    CASE
      WHEN count(*) = 8
       AND count(DISTINCT r.document_id) = 8
       AND count_if(
         r.doc_uri NOT LIKE concat(
           '/Volumes/rag_accuracy_demo/rag_accuracy/documents/projects/',
           '1f113047-82b3-4327-b2b5-7322ecd26ad9/source_pdfs/%'
         )
       ) = 0
      THEN map_from_entries(
        collect_list(named_struct('key', d.doc_code, 'value', r.doc_uri))
      )
      ELSE CAST(
        raise_error(
          'Evaluation seed aborted: D01-D08 are not completely registered in the baseline Project.'
        ) AS MAP<STRING, STRING>
      )
    END AS doc_uri_by_code
  FROM document_ids d
  JOIN rag_accuracy_demo.rag_accuracy.toyota_document_registry r
    ON r.document_id = d.document_id
   AND r.project_id = '1f113047-82b3-4327-b2b5-7322ecd26ad9'
),
raw_seed AS (
  SELECT payload
  FROM VALUES
    ('{"eval_case_id":"identifier-001","dataset_split":"development","question":"2024年式プリウスのE-Fourの型式は何ですか。","expected_answer":"ZVW65です。2WDはZVW60です。","relevance_judgments":[{"doc_id":"D01","filename":"01_prius_2024_owners_guide_demo.pdf","page_number":2,"relevance_grade":3},{"doc_id":"D08","filename":"08_toyota_demo_safety_glossary.pdf","page_number":5,"relevance_grade":2}],"expected_filter":{"model":"Prius","model_year":2024},"question_type":"identifier"}'),
    ('{"eval_case_id":"metadata-001","dataset_split":"development","question":"2024年式プリウスのPDAが作動する速度範囲を教えてください。","expected_answer":"10-60 km/hです。","relevance_judgments":[{"doc_id":"D01","filename":"01_prius_2024_owners_guide_demo.pdf","page_number":3,"relevance_grade":3},{"doc_id":"D04","filename":"04_prius_2024_safety_operation_demo.pdf","page_number":3,"relevance_grade":3}],"expected_filter":{"model":"Prius","model_year":2024},"question_type":"metadata_filter"}'),
    ('{"eval_case_id":"metadata-002","dataset_split":"development","question":"2024年式プリウスのAHSは何km/h以上で自動配光し、事前に何を操作しますか。","expected_answer":"30 km/h以上です。ライトをAUTOにし、ヘッドランプレバーを前方へ操作します。","relevance_judgments":[{"doc_id":"D01","filename":"01_prius_2024_owners_guide_demo.pdf","page_number":4,"relevance_grade":3},{"doc_id":"D04","filename":"04_prius_2024_safety_operation_demo.pdf","page_number":5,"relevance_grade":3}],"expected_filter":{"model":"Prius","model_year":2024},"question_type":"metadata_filter"}'),
    ('{"eval_case_id":"metadata-003","dataset_split":"development","question":"2023年式プリウスでPDAを有効にする操作と作動速度範囲を教えてください。","expected_answer":"DRIVE ASSISTスイッチを2秒以上長押しします。作動速度は10-40 km/hです。","relevance_judgments":[{"doc_id":"D02","filename":"02_prius_2023_owners_guide_demo.pdf","page_number":3,"relevance_grade":3}],"expected_filter":{"model":"Prius","model_year":2023},"question_type":"metadata_filter"}'),
    ('{"eval_case_id":"table-001","dataset_split":"development","question":"2024年式プリウスのGグレードにSEAは標準、オプション、設定なしのどれですか。","expected_answer":"設定なしです。","relevance_judgments":[{"doc_id":"D03","filename":"03_prius_2024_grade_equipment_demo.pdf","page_number":2,"relevance_grade":3},{"doc_id":"D03","filename":"03_prius_2024_grade_equipment_demo.pdf","page_number":1,"relevance_grade":2},{"doc_id":"D06","filename":"06_prius_2023_2024_change_report_demo.pdf","page_number":3,"relevance_grade":2}],"expected_filter":{"model":"Prius","model_year":2024,"document_type":"equipment_spec"},"question_type":"table"}'),
    ('{"eval_case_id":"table-002","dataset_split":"development","question":"2024年式プリウスGグレードでAHSを装着する条件は何ですか。","expected_answer":"AHSはオプションで、Safety Plus package（SP-24A）を選択した場合だけ装着できます。","relevance_judgments":[{"doc_id":"D03","filename":"03_prius_2024_grade_equipment_demo.pdf","page_number":4,"relevance_grade":3},{"doc_id":"D03","filename":"03_prius_2024_grade_equipment_demo.pdf","page_number":2,"relevance_grade":2}],"expected_filter":{"model":"Prius","model_year":2024,"document_type":"equipment_spec"},"question_type":"table_footnote"}'),
    ('{"eval_case_id":"table-003","dataset_split":"development","question":"2024年式プリウスZグレードでは、PDA、AHS、SEAのうち何が標準装備ですか。","expected_answer":"PDA、AHS、SEAの3機能すべてが標準装備です。","relevance_judgments":[{"doc_id":"D03","filename":"03_prius_2024_grade_equipment_demo.pdf","page_number":2,"relevance_grade":3},{"doc_id":"D06","filename":"06_prius_2023_2024_change_report_demo.pdf","page_number":3,"relevance_grade":2}],"expected_filter":{"model":"Prius","model_year":2024,"document_type":"equipment_spec"},"question_type":"table"}'),
    ('{"eval_case_id":"figure-001","dataset_split":"development","question":"2024年式プリウスの安全センサー図で、②は何を示しますか。","expected_answer":"ミリ波レーダーです。","relevance_judgments":[{"doc_id":"D04","filename":"04_prius_2024_safety_operation_demo.pdf","page_number":2,"relevance_grade":3}],"expected_filter":{"model":"Prius","model_year":2024,"document_type":"safety_operation_guide"},"question_type":"figure"}'),
    ('{"eval_case_id":"figure-002","dataset_split":"development","question":"ZVW60のレスキュー配置図で、①の部品名と場所を教えてください。","expected_answer":"サービスプラグで、後席座面下にあります。","relevance_judgments":[{"doc_id":"D05","filename":"05_prius_2024_emergency_response_demo.pdf","page_number":3,"relevance_grade":3},{"doc_id":"D05","filename":"05_prius_2024_emergency_response_demo.pdf","page_number":2,"relevance_grade":2}],"expected_filter":{"model":"Prius","model_year":2024,"document_type":"emergency_response_guide"},"question_type":"figure"}'),
    ('{"eval_case_id":"procedure-001","dataset_split":"development","question":"ZVW60の救援作業で高電圧を遮断する手順を、待機時間を含めて順番に教えてください。","expected_answer":"READYをOFFにし、電子キーを車両から5 m以上離し、12Vバッテリーのマイナス端子を切り離し、サービスプラグを取り外し、10分間待機します。","relevance_judgments":[{"doc_id":"D05","filename":"05_prius_2024_emergency_response_demo.pdf","page_number":4,"relevance_grade":3},{"doc_id":"D05","filename":"05_prius_2024_emergency_response_demo.pdf","page_number":3,"relevance_grade":2}],"expected_filter":{"model":"Prius","model_year":2024,"document_type":"emergency_response_guide"},"question_type":"procedure"}'),
    ('{"eval_case_id":"parent-child-001","dataset_split":"development","question":"2024年式プリウスのPDAはセンサーが遮られているとき使用できますか。また、有効化と解除の操作は何ですか。","expected_answer":"センサーが遮られていると使用できません。有効化は「設定 > 運転支援 > PDA」、一時解除はステアリングのcancel switchを1回押します。","relevance_judgments":[{"doc_id":"D04","filename":"04_prius_2024_safety_operation_demo.pdf","page_number":3,"relevance_grade":3},{"doc_id":"D04","filename":"04_prius_2024_safety_operation_demo.pdf","page_number":4,"relevance_grade":3}],"expected_filter":{"model":"Prius","model_year":2024,"document_type":"safety_operation_guide"},"question_type":"parent_child"}'),
    ('{"eval_case_id":"query-opt-001","dataset_split":"development","question":"「先読み運転支援」は2023年式から2024年式のプリウスで、作動速度がどのように変わりましたか。","expected_answer":"先読み運転支援はPDAです。作動速度は10-40 km/hから10-60 km/hへ変わり、下限は同じで上限が20 km/h拡大しました。","relevance_judgments":[{"doc_id":"D01","filename":"01_prius_2024_owners_guide_demo.pdf","page_number":3,"relevance_grade":3},{"doc_id":"D02","filename":"02_prius_2023_owners_guide_demo.pdf","page_number":3,"relevance_grade":3},{"doc_id":"D06","filename":"06_prius_2023_2024_change_report_demo.pdf","page_number":2,"relevance_grade":3},{"doc_id":"D08","filename":"08_toyota_demo_safety_glossary.pdf","page_number":2,"relevance_grade":2}],"expected_filter":{"model":"Prius"},"question_type":"query_optimization"}'),
    ('{"eval_case_id":"query-opt-002","dataset_split":"holdout","question":"プリウスの「配光支援」は2023年式と2024年式で開始速度がどう変わり、操作条件は変わりましたか。","expected_answer":"配光支援はAHSです。開始速度は35 km/h以上から30 km/h以上へ5 km/h下がり、AUTOとレバー前方という操作条件は同じです。","relevance_judgments":[{"doc_id":"D01","filename":"01_prius_2024_owners_guide_demo.pdf","page_number":4,"relevance_grade":3},{"doc_id":"D02","filename":"02_prius_2023_owners_guide_demo.pdf","page_number":4,"relevance_grade":3},{"doc_id":"D06","filename":"06_prius_2023_2024_change_report_demo.pdf","page_number":5,"relevance_grade":2},{"doc_id":"D08","filename":"08_toyota_demo_safety_glossary.pdf","page_number":3,"relevance_grade":2}],"expected_filter":{"model":"Prius"},"question_type":"query_optimization"}'),
    ('{"eval_case_id":"multi-table-001","dataset_split":"holdout","question":"2024年式プリウスのZとGで、PDA、AHS、SEAの装備差を比較してください。","expected_answer":"Zは3機能すべて標準です。GはPDAが標準、AHSはSafety Plus package選択時だけオプション、SEAは設定なしです。","relevance_judgments":[{"doc_id":"D03","filename":"03_prius_2024_grade_equipment_demo.pdf","page_number":2,"relevance_grade":3},{"doc_id":"D03","filename":"03_prius_2024_grade_equipment_demo.pdf","page_number":4,"relevance_grade":3},{"doc_id":"D06","filename":"06_prius_2023_2024_change_report_demo.pdf","page_number":3,"relevance_grade":2}],"expected_filter":{"model":"Prius","model_year":2024,"document_type":"equipment_spec"},"question_type":"multi_table"}'),
    ('{"eval_case_id":"ambiguous-001","dataset_split":"holdout","question":"PDAの作動上限速度は何km/hですか。","expected_answer":"車種と年式で異なるため一意に回答できません。2023 Priusは40 km/h、2024 Priusは60 km/h、2024 Crown Sportは50 km/hです。","relevance_judgments":[{"doc_id":"D01","filename":"01_prius_2024_owners_guide_demo.pdf","page_number":3,"relevance_grade":3},{"doc_id":"D02","filename":"02_prius_2023_owners_guide_demo.pdf","page_number":3,"relevance_grade":3},{"doc_id":"D07","filename":"07_crown_sport_2024_owners_guide_demo.pdf","page_number":3,"relevance_grade":3}],"expected_filter":null,"question_type":"clarification","exclude_from_retrieval_metrics":true}'),
    ('{"eval_case_id":"unanswerable-001","dataset_split":"holdout","question":"2024年式プリウスの新車価格とWLTC燃費を教えてください。","expected_answer":"この資料群には新車価格とWLTC燃費が記載されていないため回答できません。","relevance_judgments":[],"expected_filter":{"model":"Prius","model_year":2024},"question_type":"unanswerable","exclude_from_retrieval_metrics":true}')
  AS seed(payload)
),
parsed_seed AS (
  SELECT from_json(
    payload,
    'STRUCT<eval_case_id:STRING,dataset_split:STRING,question:STRING,expected_answer:STRING,relevance_judgments:ARRAY<STRUCT<doc_id:STRING,filename:STRING,page_number:INT,relevance_grade:INT>>,expected_filter:STRUCT<model:STRING,model_year:INT,document_type:STRING,vehicle_category:STRING>,question_type:STRING,exclude_from_retrieval_metrics:BOOLEAN>'
  ) AS record
  FROM raw_seed
),
resolved AS (
  SELECT
    s.record,
    transform(
      s.record.relevance_judgments,
      judgment -> named_struct(
        'doc_uri', coalesce(
          element_at(m.doc_uri_by_code, judgment.doc_id),
          CAST(
            raise_error(concat('Unknown qrel document code: ', judgment.doc_id))
            AS STRING
          )
        ),
        'page_number', judgment.page_number,
        'relevance_grade', judgment.relevance_grade
      )
    ) AS qrels
  FROM parsed_seed s
  CROSS JOIN registry_map m
),
source AS (
  SELECT
    '1f113047-82b3-4327-b2b5-7322ecd26ad9' AS project_id,
    record.eval_case_id AS eval_case_id,
    record.question AS question,
    record.expected_answer AS expected_answer,
    CAST(NULL AS ARRAY<STRING>) AS expected_facts,
    element_at(
      transform(
        filter(
          qrels,
          qrel -> qrel.relevance_grade = array_max(
            transform(qrels, candidate -> candidate.relevance_grade)
          )
        ),
        qrel -> qrel.doc_uri
      ),
      1
    ) AS relevant_doc_uri,
    array_sort(
      array_distinct(
        transform(
          filter(qrels, qrel -> qrel.relevance_grade >= 2),
          qrel -> qrel.page_number
        )
      )
    ) AS relevant_pages,
    qrels AS relevance_judgments,
    record.expected_filter AS expected_filter,
    record.expected_filter.model AS model,
    record.expected_filter.model_year AS model_year,
    record.question_type AS question_type,
    record.question_type <> 'unanswerable' AS is_answerable,
    'ja' AS language,
    'v1.0.0' AS dataset_version,
    record.dataset_split AS dataset_split
  FROM resolved
)
MERGE INTO rag_accuracy_demo.rag_accuracy.toyota_rag_eval_cases AS target
USING source
ON target.project_id = source.project_id
AND target.eval_case_id = source.eval_case_id
WHEN MATCHED THEN UPDATE SET
  target.question = source.question,
  target.expected_answer = source.expected_answer,
  target.expected_facts = source.expected_facts,
  target.relevant_doc_uri = source.relevant_doc_uri,
  target.relevant_pages = source.relevant_pages,
  target.relevance_judgments = source.relevance_judgments,
  target.expected_filter = source.expected_filter,
  target.model = source.model,
  target.model_year = source.model_year,
  target.question_type = source.question_type,
  target.is_answerable = source.is_answerable,
  target.language = source.language,
  target.dataset_version = source.dataset_version,
  target.dataset_split = source.dataset_split
WHEN NOT MATCHED THEN INSERT (
  project_id, eval_case_id, question, expected_answer, expected_facts,
  relevant_doc_uri, relevant_pages, relevance_judgments, expected_filter,
  model, model_year, question_type, is_answerable, language,
  dataset_version, dataset_split, created_at
) VALUES (
  source.project_id, source.eval_case_id, source.question,
  source.expected_answer, source.expected_facts, source.relevant_doc_uri,
  source.relevant_pages, source.relevance_judgments, source.expected_filter,
  source.model, source.model_year, source.question_type,
  source.is_answerable, source.language, source.dataset_version,
  source.dataset_split, current_timestamp()
);

-- Verification 1: exactly 16 fixed cases (12 development, 4 holdout).
SELECT
  count(*) AS case_count,
  count_if(dataset_split = 'development') AS development_count,
  count_if(dataset_split = 'holdout') AS holdout_count,
  count_if(is_answerable) AS answerable_count,
  count_if(NOT is_answerable) AS unanswerable_count,
  sum(size(relevance_judgments)) AS qrel_count
FROM rag_accuracy_demo.rag_accuracy.toyota_rag_eval_cases
WHERE project_id = '1f113047-82b3-4327-b2b5-7322ecd26ad9'
  AND dataset_version = 'v1.0.0';

-- Expected: invalid_case_count = 0.  All qrels must resolve to D01-D08 Volume URIs.
SELECT count(*) AS invalid_case_count
FROM rag_accuracy_demo.rag_accuracy.toyota_rag_eval_cases
WHERE project_id = '1f113047-82b3-4327-b2b5-7322ecd26ad9'
  AND dataset_version = 'v1.0.0'
  AND (
    size(filter(relevance_judgments, qrel -> qrel.doc_uri IS NULL)) > 0
    OR size(filter(
      relevance_judgments,
      qrel -> qrel.doc_uri NOT LIKE concat(
        '/Volumes/rag_accuracy_demo/rag_accuracy/documents/projects/',
        '1f113047-82b3-4327-b2b5-7322ecd26ad9/source_pdfs/%'
      )
    )) > 0
    OR (is_answerable AND question_type <> 'clarification' AND size(relevance_judgments) = 0)
    OR (NOT is_answerable AND size(relevance_judgments) <> 0)
  );
