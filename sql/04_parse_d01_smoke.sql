-- Parse one baseline PDF first. The input is FILE EXTERNAL, never BINARY.

MERGE INTO rag_accuracy_demo.rag_accuracy.toyota_parsed_v2 AS target
USING (
  SELECT
    r.project_id,
    r.document_id,
    regexp_replace(f.source_file.uri, '^dbfs:', '') AS doc_uri,
    f.modification_time AS source_modified_at,
    f.size AS source_size_bytes,
    ai_parse_document(
      f.source_file,
      map(
        'version', '2.0',
        'imageOutputPath', '/Volumes/rag_accuracy_demo/rag_accuracy/documents/projects/1f113047-82b3-4327-b2b5-7322ecd26ad9/page_images/9ea03add-de22-4c9a-8cac-c8de8e234d6a/',
        'descriptionElementTypes', '*'
      )
    ) AS parsed,
    current_timestamp() AS parsed_at
  FROM (
    SELECT size, modification_time, file AS source_file
    FROM READ_FILES(
      '/Volumes/rag_accuracy_demo/rag_accuracy/documents/projects/1f113047-82b3-4327-b2b5-7322ecd26ad9/source_pdfs/9ea03add-de22-4c9a-8cac-c8de8e234d6a.pdf',
      format => 'file'
    )
  ) AS f
  JOIN rag_accuracy_demo.rag_accuracy.toyota_document_registry AS r
    ON regexp_replace(f.source_file.uri, '^dbfs:', '') = r.doc_uri
  WHERE r.project_id = '1f113047-82b3-4327-b2b5-7322ecd26ad9'
    AND r.document_id = '9ea03add-de22-4c9a-8cac-c8de8e234d6a'
) AS source
ON target.project_id = source.project_id AND target.document_id = source.document_id
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *;

UPDATE rag_accuracy_demo.rag_accuracy.toyota_document_registry
SET processing_status = 'PARSED',
    processing_message = 'D01 parsed with FILE input and schema 2.0',
    source_modified_at = (
      SELECT source_modified_at
      FROM rag_accuracy_demo.rag_accuracy.toyota_parsed_v2
      WHERE document_id = '9ea03add-de22-4c9a-8cac-c8de8e234d6a'
    )
WHERE project_id = '1f113047-82b3-4327-b2b5-7322ecd26ad9'
  AND document_id = '9ea03add-de22-4c9a-8cac-c8de8e234d6a';
