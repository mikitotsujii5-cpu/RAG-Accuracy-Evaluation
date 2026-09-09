#!/usr/bin/env python3
"""Synchronize READY Foundation Model API endpoints into the app catalog.

Only workspace-hosted Foundation Model entries are included.  Custom serving
endpoints are deliberately excluded because their input contract and Project
authorization cannot be inferred from the endpoint task alone.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from typing import Any

from execute_sql_file import execute_statement


CATALOG = "rag_accuracy_demo"
SCHEMA = "rag_accuracy"
TABLE = f"{CATALOG}.{SCHEMA}.toyota_rag_model_catalog"

# These endpoints are used by the verified Custom Agent path.  All other
# READY chat models remain selectable for deterministic RAG and evaluation.
TOOL_CALLING_VERIFIED = {
    "databricks-gpt-5-6-luna",
    "databricks-gpt-5-6-sol",
    "databricks-gpt-5-6-terra",
}


def cli_json(args: list[str]) -> Any:
    completed = subprocess.run(
        ["databricks", *args], check=False, capture_output=True, text=True
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return json.loads(completed.stdout)


def sql_string(value: str | None) -> str:
    if value is None:
        return "NULL"
    return "'" + value.replace("'", "''") + "'"


def model_key(prefix: str, endpoint_name: str) -> str:
    stem = endpoint_name.removeprefix("databricks-")
    # Keep the original, already-referenced key for Qwen3 Embedding stable.
    if prefix == "emb":
        stem = stem.replace("-embedding-", "-")
    candidate = f"{prefix}-{stem}"
    if len(candidate) <= 128:
        return candidate
    digest = hashlib.sha256(endpoint_name.encode()).hexdigest()[:12]
    return f"{candidate[:115]}-{digest}"


def discover(profile: str) -> list[dict[str, Any]]:
    endpoints = cli_json(["serving-endpoints", "list", "-p", profile, "-o", "json"])
    discovered: list[dict[str, Any]] = []
    for endpoint in endpoints:
        task = endpoint.get("task")
        state = (endpoint.get("state") or {}).get("ready")
        entities = ((endpoint.get("config") or {}).get("served_entities") or [])
        foundation = (entities[0].get("foundation_model") or {}) if entities else {}
        if state != "READY" or not foundation:
            continue
        if task == "llm/v1/embeddings":
            capabilities = ["embedding"]
            prefix = "emb"
        elif task == "llm/v1/chat":
            capabilities = ["chat", "streaming", "judge", "advisor"]
            if endpoint["name"] in TOOL_CALLING_VERIFIED:
                capabilities.extend(["chat_tool_calling", "tool_calling"])
            prefix = "llm"
        else:
            continue
        discovered.append(
            {
                "model_key": model_key(prefix, endpoint["name"]),
                "display_name": foundation.get("display_name") or endpoint["name"],
                "target_name": endpoint["name"],
                "capabilities": capabilities,
            }
        )
    return sorted(discovered, key=lambda item: (item["capabilities"][0], item["display_name"]))


def merge_sql(models: list[dict[str, Any]]) -> str:
    if not models:
        raise RuntimeError("READY Foundation Model API endpointが見つかりません")
    values = []
    for item in models:
        caps = "array(" + ", ".join(sql_string(cap) for cap in item["capabilities"]) + ")"
        values.append(
            "(" + ", ".join(
                [
                    sql_string(item["model_key"]),
                    sql_string(item["display_name"]),
                    sql_string(item["target_name"]),
                    caps,
                ]
            ) + ")"
        )
    return f"""
MERGE INTO {TABLE} AS target
USING (
  SELECT * FROM VALUES
    {',\n    '.join(values)}
  AS source(model_key, display_name, target_name, capabilities)
) AS source
ON target.model_key = source.model_key
WHEN MATCHED THEN UPDATE SET
  target.display_name = source.display_name,
  target.target_kind = 'FMAPI_ENDPOINT',
  target.target_name = source.target_name,
  target.capabilities = source.capabilities,
  target.endpoint_state = 'READY',
  target.region_available = TRUE,
  target.selectable = TRUE,
  target.unavailable_reason = NULL,
  target.verified_at = current_timestamp()
WHEN NOT MATCHED THEN INSERT (
  model_key, display_name, target_kind, target_name, capabilities,
  endpoint_state, embedding_dimension, max_context_tokens, max_output_tokens,
  region_available, selectable, unavailable_reason, verified_at
) VALUES (
  source.model_key, source.display_name, 'FMAPI_ENDPOINT', source.target_name,
  source.capabilities, 'READY', NULL, NULL, NULL, TRUE, TRUE, NULL,
  current_timestamp()
)
WHEN NOT MATCHED BY SOURCE AND target.target_kind = 'FMAPI_ENDPOINT' THEN UPDATE SET
  target.endpoint_state = 'UNAVAILABLE',
  target.region_available = FALSE,
  target.selectable = FALSE,
  target.unavailable_reason = '現在のWorkspaceでREADYなFMAPI endpointとして検出されませんでした',
  target.verified_at = current_timestamp()
""".strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--warehouse-id", required=True)
    args = parser.parse_args()

    models = discover(args.profile)
    response = execute_statement(
        merge_sql(models),
        profile=args.profile,
        warehouse_id=args.warehouse_id,
        catalog=CATALOG,
        schema=SCHEMA,
    )
    state = response.get("status", {}).get("state")
    print(
        json.dumps(
            {
                "state": state,
                "statement_id": response.get("statement_id"),
                "foundation_models": len(models),
                "chat_models": sum("chat" in item["capabilities"] for item in models),
                "embedding_models": sum("embedding" in item["capabilities"] for item in models),
                "error": response.get("status", {}).get("error"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if state == "SUCCEEDED" else 1


if __name__ == "__main__":
    sys.exit(main())
