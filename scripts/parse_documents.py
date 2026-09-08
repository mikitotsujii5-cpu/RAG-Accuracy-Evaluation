#!/usr/bin/env python3
"""Parse the synthetic corpus through ai_parse_document using FILE EXTERNAL.

This deployment helper submits one document per SQL statement. A failure stops
the run before the next document, which keeps the step-by-step verification
contract explicit and makes retries idempotent through MERGE.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass

from execute_sql_file import execute_statement


CATALOG = "mikito_toyota_rag_eval"
SCHEMA = "rag_accuracy"
VOLUME_ROOT = f"/Volumes/{CATALOG}/{SCHEMA}/documents"


@dataclass(frozen=True)
class Document:
    code: str
    project_id: str
    document_id: str


DOCUMENTS = (
    Document("D01", "1f113047-82b3-4327-b2b5-7322ecd26ad9", "9ea03add-de22-4c9a-8cac-c8de8e234d6a"),
    Document("D02", "1f113047-82b3-4327-b2b5-7322ecd26ad9", "dffd3261-c8b4-4a85-b1d6-a74576dbdb89"),
    Document("D03", "1f113047-82b3-4327-b2b5-7322ecd26ad9", "00d0cbab-5813-45ea-a4e9-775e9056ad58"),
    Document("D04", "1f113047-82b3-4327-b2b5-7322ecd26ad9", "1a6b0abc-e220-4dd5-85ba-1a6a07baee67"),
    Document("D05", "1f113047-82b3-4327-b2b5-7322ecd26ad9", "031bab44-de2c-47d0-a382-8736da861416"),
    Document("D06", "1f113047-82b3-4327-b2b5-7322ecd26ad9", "36c77b23-77d8-442d-9e1a-d3330e907490"),
    Document("D07", "1f113047-82b3-4327-b2b5-7322ecd26ad9", "247c8253-4187-445b-bb75-1d0180e54830"),
    Document("D08", "1f113047-82b3-4327-b2b5-7322ecd26ad9", "2715b88e-0c3a-4eec-ac4a-f5030b659761"),
    Document("D09", "ba8848ed-845b-49ed-9cd3-42aef3c0ad60", "be11847e-a7e7-4166-b9b7-f093712cd3d3"),
)


def mkdir(profile: str, path: str) -> None:
    completed = subprocess.run(
        ["databricks", "fs", "mkdir", f"dbfs:{path}", "-p", profile],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())


def parse_sql(document: Document) -> str:
    source_path = (
        f"{VOLUME_ROOT}/projects/{document.project_id}/source_pdfs/"
        f"{document.document_id}.pdf"
    )
    image_path = (
        f"{VOLUME_ROOT}/projects/{document.project_id}/page_images/"
        f"{document.document_id}/"
    )
    return f"""
MERGE INTO {CATALOG}.{SCHEMA}.toyota_parsed_v2 AS target
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
        'imageOutputPath', '{image_path}',
        'descriptionElementTypes', '*'
      )
    ) AS parsed,
    current_timestamp() AS parsed_at
  FROM (
    SELECT size, modification_time, file AS source_file
    FROM READ_FILES('{source_path}', format => 'file')
  ) AS f
  JOIN {CATALOG}.{SCHEMA}.toyota_document_registry AS r
    ON regexp_replace(f.source_file.uri, '^dbfs:', '') = r.doc_uri
  WHERE r.project_id = '{document.project_id}'
    AND r.document_id = '{document.document_id}'
) AS source
ON target.project_id = source.project_id AND target.document_id = source.document_id
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
""".strip()


def registry_update_sql(document: Document) -> str:
    return f"""
UPDATE {CATALOG}.{SCHEMA}.toyota_document_registry
SET processing_status = 'PARSED',
    processing_message = '{document.code} parsed with FILE input and schema 2.0',
    source_modified_at = (
      SELECT source_modified_at FROM {CATALOG}.{SCHEMA}.toyota_parsed_v2
      WHERE project_id = '{document.project_id}' AND document_id = '{document.document_id}'
    )
WHERE project_id = '{document.project_id}' AND document_id = '{document.document_id}'
""".strip()


def validation_sql(document: Document) -> str:
    return f"""
SELECT
  document_id,
  parsed:metadata:version::STRING AS schema_version,
  size(from_json(to_json(parsed:document:pages), 'ARRAY<VARIANT>')) AS page_count,
  size(from_json(to_json(parsed:document:elements), 'ARRAY<VARIANT>')) AS element_count,
  to_json(parsed:error_status) AS errors
FROM {CATALOG}.{SCHEMA}.toyota_parsed_v2
WHERE project_id = '{document.project_id}' AND document_id = '{document.document_id}'
""".strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument(
        "--documents",
        default="D02,D03,D04,D05,D06,D07,D08,D09",
        help="Comma-separated document codes",
    )
    args = parser.parse_args()
    requested = {item.strip().upper() for item in args.documents.split(",") if item.strip()}
    known = {document.code for document in DOCUMENTS}
    unknown = requested - known
    if unknown:
        raise SystemExit(f"Unknown document codes: {sorted(unknown)}")

    for document in DOCUMENTS:
        if document.code not in requested:
            continue
        image_path = (
            f"{VOLUME_ROOT}/projects/{document.project_id}/page_images/"
            f"{document.document_id}"
        )
        mkdir(args.profile, image_path)
        for stage, sql in (
            ("parse", parse_sql(document)),
            ("registry", registry_update_sql(document)),
            ("validate", validation_sql(document)),
        ):
            response = execute_statement(
                sql,
                profile=args.profile,
                warehouse_id=args.warehouse_id,
                catalog=CATALOG,
                schema=SCHEMA,
            )
            state = response.get("status", {}).get("state")
            result = response.get("result", {}).get("data_array")
            print(
                json.dumps(
                    {
                        "document": document.code,
                        "stage": stage,
                        "statement_id": response.get("statement_id"),
                        "state": state,
                        "result": result,
                        "error": response.get("status", {}).get("error"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if state != "SUCCEEDED":
                return 1
            if stage == "validate":
                if not result or result[0][1] != "2.0" or int(result[0][2]) != 5 or result[0][4] not in {None, "null"}:
                    print(f"Validation failed for {document.code}: {result}", file=sys.stderr)
                    return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
