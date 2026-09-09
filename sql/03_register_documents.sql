-- Registry rows match the nine uploaded PDFs. D09 is isolated from the baseline Project.

MERGE INTO rag_accuracy_demo.rag_accuracy.toyota_document_registry AS target
USING (
  SELECT * FROM VALUES
    ('9ea03add-de22-4c9a-8cac-c8de8e234d6a', 'D01', '1f113047-82b3-4327-b2b5-7322ecd26ad9', '01_prius_2024_owners_guide_demo.pdf', 'Prius 2024 取扱クイックガイド', '2024年式Priusの安全機能を確認する取扱資料', 'Prius', 2024, 'owners_guide', 'passenger_car', '6580d949e2ea7e6931055c094f2198dc2fcff07cf9f6c68cdb1801e1ba1d55c7', 161262),
    ('dffd3261-c8b4-4a85-b1d6-a74576dbdb89', 'D02', '1f113047-82b3-4327-b2b5-7322ecd26ad9', '02_prius_2023_owners_guide_demo.pdf', 'Prius 2023 取扱クイックガイド', '2023年式Priusの安全機能を比較する取扱資料', 'Prius', 2023, 'owners_guide', 'passenger_car', '1d20dd046737d1119320daed02af107ee50c66d6fde98ecd21eaea3499822053', 137918),
    ('00d0cbab-5813-45ea-a4e9-775e9056ad58', 'D03', '1f113047-82b3-4327-b2b5-7322ecd26ad9', '03_prius_2024_grade_equipment_demo.pdf', 'Prius 2024 グレード別装備一覧', 'Priusのグレード別装備差を表で確認する資料', 'Prius', 2024, 'equipment_spec', 'passenger_car', 'a0e6682da0fc5a9ecf3216c81e73296e3bc47810c49e6434e5f6622b781c59b7', 147326),
    ('1a6b0abc-e220-4dd5-85ba-1a6a07baee67', 'D04', '1f113047-82b3-4327-b2b5-7322ecd26ad9', '04_prius_2024_safety_operation_demo.pdf', 'Prius 2024 安全支援 操作ガイド', 'Priusの安全支援条件と操作手順を確認する資料', 'Prius', 2024, 'safety_operation_guide', 'passenger_car', '54fbc78ed602f63c3600d17520a161d7d43a74f1f7f9f4fa8543723d8b50e174', 158543),
    ('031bab44-de2c-47d0-a382-8736da861416', 'D05', '1f113047-82b3-4327-b2b5-7322ecd26ad9', '05_prius_2024_emergency_response_demo.pdf', 'Prius 2024 緊急時対応・レスキューガイド', 'Priusの緊急対応手順と部品配置を確認する資料', 'Prius', 2024, 'emergency_response_guide', 'passenger_car', 'bbe3a62a70a3c18855799aef1cb147d3b825239bd8827e7591e7bd4404553873', 150629),
    ('36c77b23-77d8-442d-9e1a-d3330e907490', 'D06', '1f113047-82b3-4327-b2b5-7322ecd26ad9', '06_prius_2023_2024_change_report_demo.pdf', 'Prius 2023-2024 変更点レポート', 'Priusの年式別変更点を比較するレポート資料', 'Prius', 2024, 'model_change_report', 'passenger_car', '7eda3cf45cc427e85e176c7137770921ad62667aefe560e03718ee8ff393d048', 152023),
    ('247c8253-4187-445b-bb75-1d0180e54830', 'D07', '1f113047-82b3-4327-b2b5-7322ecd26ad9', '07_crown_sport_2024_owners_guide_demo.pdf', 'Crown Sport 2024 取扱クイックガイド', 'Crown Sportの安全機能を確認する取扱資料', 'Crown Sport', 2024, 'owners_guide', 'suv', 'c0971d05aac290f605be079db88983c6ea2809651673e1773539c77d7df4cf3b', 116260),
    ('2715b88e-0c3a-4eec-ac4a-f5030b659761', 'D08', '1f113047-82b3-4327-b2b5-7322ecd26ad9', '08_toyota_demo_safety_glossary.pdf', '安全支援用語集・型式索引', '安全支援機能の略称と用語を確認する共通資料', 'Common', CAST(NULL AS INT), 'glossary', 'all', '4eb5acf49119c77f000b4f2eb6c55be685c00f3158b690db0c3134b4b26abfce', 137080),
    ('be11847e-a7e7-4166-b9b7-f093712cd3d3', 'D09', 'ba8848ed-845b-49ed-9cd3-42aef3c0ad60', '09_prius_2024_emergency_response_scan_demo.pdf', 'Prius 2024 緊急時対応・レスキューガイド スキャン風版', '画像化PDFの文書解析精度を比較する検証資料', 'Prius', 2024, 'emergency_response_scan', 'passenger_car', 'f146a9303cc10af827cf601cb4956ff5d7caa612621e91543607f0ab7c764807', 1092817)
  AS source(
    document_id, doc_code, project_id, original_filename, title, summary,
    model, model_year, document_type, vehicle_category, sha256, source_size_bytes
  )
) AS source
ON target.document_id = source.document_id AND target.project_id = source.project_id
WHEN MATCHED THEN UPDATE SET
  target.doc_uri = concat('/Volumes/rag_accuracy_demo/rag_accuracy/documents/projects/', source.project_id, '/source_pdfs/', source.document_id, '.pdf'),
  target.original_filename = source.original_filename,
  target.title = source.title,
  target.summary = source.summary,
  target.summary_source = 'USER',
  target.summary_status = 'READY',
  target.page_count = 5,
  target.model = source.model,
  target.model_year = source.model_year,
  target.document_type = source.document_type,
  target.vehicle_category = source.vehicle_category,
  target.category = source.document_type,
  target.tags = CAST(array() AS ARRAY<STRING>),
  target.document_date = CAST(NULL AS DATE),
  target.source = 'Synthetic RAG evaluation PDF',
  target.metadata_json = to_json(named_struct(
    'schema_version', '1.0',
    'common', named_struct(
      'title', source.title,
      'category', source.document_type,
      'tags', CAST(array() AS ARRAY<STRING>),
      'document_date', CAST(NULL AS STRING),
      'source', 'Synthetic RAG evaluation PDF'
    ),
    'custom', map_from_arrays(
      CAST(array() AS ARRAY<STRING>), CAST(array() AS ARRAY<STRING>)
    ),
    'legacy', named_struct(
      'model', source.model,
      'model_year', source.model_year,
      'document_type', source.document_type,
      'vehicle_category', source.vehicle_category
    )
  )),
  target.language = 'ja',
  target.sha256 = source.sha256,
  target.source_size_bytes = source.source_size_bytes,
  target.uploaded_by = current_user(),
  target.processing_status = 'UPLOADED',
  target.processing_message = concat(source.doc_code, ' uploaded and awaiting FILE parsing')
WHEN NOT MATCHED THEN INSERT (
  document_id, project_id, doc_uri, original_filename, title, summary,
  summary_source, summary_status, page_count, model, model_year,
  document_type, vehicle_category, category, tags, document_date, source,
  metadata_json, language, sha256, source_size_bytes,
  uploaded_by, uploaded_at, processing_status, processing_message
) VALUES (
  source.document_id, source.project_id,
  concat('/Volumes/rag_accuracy_demo/rag_accuracy/documents/projects/', source.project_id, '/source_pdfs/', source.document_id, '.pdf'),
  source.original_filename, source.title, source.summary, 'USER', 'READY', 5,
  source.model, source.model_year, source.document_type,
  source.vehicle_category, source.document_type,
  CAST(array() AS ARRAY<STRING>),
  CAST(NULL AS DATE),
  'Synthetic RAG evaluation PDF',
  to_json(named_struct(
    'schema_version', '1.0',
    'common', named_struct(
      'title', source.title, 'category', source.document_type,
      'tags', CAST(array() AS ARRAY<STRING>),
      'document_date', CAST(NULL AS STRING),
      'source', 'Synthetic RAG evaluation PDF'
    ),
    'custom', map_from_arrays(
      CAST(array() AS ARRAY<STRING>), CAST(array() AS ARRAY<STRING>)
    ),
    'legacy', named_struct(
      'model', source.model, 'model_year', source.model_year,
      'document_type', source.document_type,
      'vehicle_category', source.vehicle_category
    )
  )),
  'ja', source.sha256, source.source_size_bytes,
  current_user(), current_timestamp(), 'UPLOADED',
  concat(source.doc_code, ' uploaded and awaiting FILE parsing')
);
