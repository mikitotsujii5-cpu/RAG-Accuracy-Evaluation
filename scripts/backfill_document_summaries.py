#!/usr/bin/env python3
"""Backfill missing catalog summaries through the App's verified LLM path."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from gateway import DatabricksGateway, sql_string  # noqa: E402
from repository import (  # noqa: E402
    Repository,
    _model_text,
    _normalize_generated_summary,
    _normalize_llm_candidate,
)
from settings import Settings  # noqa: E402


CATALOG = "rag_accuracy_demo"
SCHEMA = "rag_accuracy"
DEFAULT_LLM_ENDPOINT = "databricks-gpt-5-6-luna"
SUMMARY_PROMPT_VERSION = "document-summary-ja-v2-two-pass"
NORMALIZED_PROMPT_VERSION = (
    "document-summary-ja-v2-two-pass+deterministic-boundary-v1"
)
SUMMARY_MAX_TOKENS = 900


def settings(warehouse_id: str) -> Settings:
    return Settings(
        warehouse_id=warehouse_id,
        uc_catalog=CATALOG,
        uc_schema=SCHEMA,
        uc_volume="documents",
        vector_search_endpoint="toyota-rag-search",
        default_index_name=(
            "rag_accuracy_demo.rag_accuracy."
            "toyota_chunks_standard_512_v1_index"
        ),
        default_llm_endpoint=DEFAULT_LLM_ENDPOINT,
        prep_job_id=None,
        eval_job_id=None,
        databricks_host=None,
        app_port=8000,
        local_principal=None,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--warehouse-id",
        default=os.environ.get("DATABRICKS_WAREHOUSE_ID"),
        help=(
            "Databricks SQL Warehouse ID. If omitted, "
            "DATABRICKS_WAREHOUSE_ID must be set."
        ),
    )
    parser.add_argument("--document-id")
    parser.add_argument(
        "--include-invalid-length",
        action="store_true",
        help="Regenerate existing summaries that are outside the 20-30 character catalog contract.",
    )
    args = parser.parse_args()
    if not args.warehouse_id:
        parser.error(
            "--warehouse-id is required when DATABRICKS_WAREHOUSE_ID is not set"
        )
    os.environ["DATABRICKS_CONFIG_PROFILE"] = args.profile

    app_settings = settings(args.warehouse_id)
    repository = Repository(app_settings, DatabricksGateway(app_settings))
    registry = repository.table("toyota_document_registry")
    parsed = repository.table("toyota_parsed_v2")
    document_filter = (
        f"AND r.document_id={sql_string(args.document_id)}"
        if args.document_id
        else ""
    )
    summary_filter = "(r.summary IS NULL OR trim(r.summary)='')"
    update_summary_filter = "(summary IS NULL OR trim(summary)='')"
    if args.include_invalid_length:
        summary_filter = (
            "(r.summary IS NULL OR trim(r.summary)='' "
            "OR length(r.summary) < 20 OR length(r.summary) > 30)"
        )
        update_summary_filter = (
            "(summary IS NULL OR trim(summary)='' "
            "OR length(summary) < 20 OR length(summary) > 30)"
        )
    rows = repository.gateway.query(
        f"""
        SELECT r.project_id, r.document_id, r.title, r.original_filename,
          substring(
            trim(regexp_replace(
              concat_ws(' ', transform(
                from_json(
                  to_json(p.parsed:document:elements),
                  'ARRAY<STRUCT<content:STRING,description:STRING>>'
                ),
                element -> coalesce(
                  nullif(trim(element.content), ''),
                  nullif(trim(element.description), ''),
                  ''
                )
              )),
              '\\s+', ' '
            )),
            1, 6000
          ) AS parsed_text
        FROM {registry} r
        INNER JOIN {parsed} p
          ON p.project_id=r.project_id AND p.document_id=r.document_id
        WHERE {summary_filter}
          {document_filter}
        ORDER BY r.project_id, r.document_id
        """
    )
    results: list[dict[str, object]] = []
    model_rows = repository.gateway.query(
        f"""
        SELECT model_key, target_name
        FROM {repository.table('toyota_rag_model_catalog')}
        WHERE target_kind='FMAPI_ENDPOINT'
          AND target_name={sql_string(DEFAULT_LLM_ENDPOINT)}
          AND endpoint_state='READY' AND selectable=TRUE AND region_available=TRUE
          AND array_contains(capabilities, 'chat')
        LIMIT 1
        """
    )
    if len(model_rows) != 1:
        raise RuntimeError("the configured summary model is not READY/selectable")
    model_key = str(model_rows[0]["model_key"])
    model_target = str(model_rows[0]["target_name"])
    for row in rows:
        title = str(row.get("title") or "PDF")
        filename = str(row.get("original_filename") or "document.pdf")
        excerpt = re.sub(r"\s+", " ", str(row.get("parsed_text") or "")).strip()[:4000]
        candidate = ""
        for _ in range(2):
            raw = repository.gateway.invoke_model(
                model_target,
                [
                    {
                        "role": "system",
                        "content": "あなたはEnterprise文書カタログの編集者です。",
                    },
                    {
                        "role": "user",
                        "content": (
                            "次のPDFの内容だけを使い、日本語の短い概要候補を一文で作成"
                            "してください。推測、前置き、引用符は不要です。\n"
                            f"タイトル: {title}\nファイル名: {filename}\n本文抜粋: {excerpt}"
                        ),
                    },
                ],
                max_tokens=SUMMARY_MAX_TOKENS,
            )
            candidate = _model_text(raw).strip()
            if candidate:
                break
        if not candidate:
            raise RuntimeError("LLM summary generation returned no text")
        candidate_length = len(candidate)
        summary: str | None = None
        for _ in range(3):
            raw = repository.gateway.invoke_model(
                model_target,
                [
                    {
                        "role": "system",
                        "content": "あなたは日本語文字数を厳密に校正する編集者です。",
                    },
                    {
                        "role": "user",
                        "content": (
                            "次の概要候補を、意味を保った日本語20字以上30字以内の一文に"
                            "校正してください。句読点も1字として数えます。前置き、文字数、"
                            "引用符は出力せず、校正後の一文だけを返してください。\n"
                            f"候補の実測文字数: {len(candidate)}\n候補: {candidate[:500]}"
                        ),
                    },
                ],
                max_tokens=SUMMARY_MAX_TOKENS,
            )
            corrected = _model_text(raw).strip()
            summary = _normalize_generated_summary(corrected)
            if summary is not None:
                break
            if corrected:
                candidate = corrected
        summary_source = "AI_GENERATED"
        prompt_version = SUMMARY_PROMPT_VERSION
        if summary is None:
            summary = _normalize_llm_candidate(candidate)
            if summary is None:
                raise RuntimeError("LLM summary candidate is empty or unsafe")
            summary_source = "AI_GENERATED_NORMALIZED"
            prompt_version = NORMALIZED_PROMPT_VERSION
        if not 20 <= len(summary) <= 30:
            raise RuntimeError("summary normalization failed the 20-30 character contract")
        response = repository.gateway.execute_sql(
            f"""
            UPDATE {registry}
            SET summary={sql_string(summary)},
                summary_source={sql_string(summary_source)},
                summary_model_key={sql_string(model_key)},
                summary_prompt_version={sql_string(prompt_version)},
                summary_status='READY'
            WHERE project_id={sql_string(str(row['project_id']))}
              AND document_id={sql_string(str(row['document_id']))}
              AND {update_summary_filter}
            """
        )
        results.append(
            {
                "project_id": row["project_id"],
                "document_id": row["document_id"],
                "candidate_length": candidate_length,
                "summary_length": len(summary),
                "summary_source": summary_source,
                "summary_model_key": model_key,
                "summary_prompt_version": prompt_version,
                "statement_id": response.get("statement_id"),
            }
        )
    print(json.dumps({"updated": len(results), "documents": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
