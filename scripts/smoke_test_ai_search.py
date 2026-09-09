#!/usr/bin/env python3
"""Exercise the exact Python AI Search API used by the App and Eval Job."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Any


INDEX_NAME = "rag_accuracy_demo.rag_accuracy.toyota_chunks_standard_512_v1_index"
ENDPOINT_NAME = "toyota-rag-search"
PROJECT_ID = "1f113047-82b3-4327-b2b5-7322ecd26ad9"
COLUMNS = [
    "chunk_id",
    "project_id",
    "document_id",
    "chunk_to_retrieve",
    "doc_uri",
    "page_number",
    "title",
    "model",
    "model_year",
    "document_type",
    "vehicle_category",
    "variant_id",
]


def as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "as_dict"):
        converted = value.as_dict()
        if isinstance(converted, dict):
            return converted
    raise TypeError("AI Search response is not mapping-like")


def rows_from_response(raw: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = as_dict(raw)
    manifest = payload.get("manifest") or {}
    columns = manifest.get("columns") or (manifest.get("schema") or {}).get("columns") or []
    names = [column.get("name") for column in columns if isinstance(column, Mapping)]
    arrays = (payload.get("result") or {}).get("data_array") or []
    rows: list[dict[str, Any]] = []
    for values in arrays:
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise ValueError("AI Search returned an invalid row")
        if len(values) != len(names):
            raise ValueError("AI Search manifest and data row lengths differ")
        rows.append(dict(zip(names, values, strict=True)))
    return rows, payload.get("debug_info") or {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    args = parser.parse_args()

    # AISearchClient 0.78 consults MLflow's Databricks credential resolver even
    # when a credential strategy is supplied.  Point both resolvers at the same
    # explicit CLI profile before importing the SDK.
    os.environ["DATABRICKS_CONFIG_PROFILE"] = args.profile
    os.environ["MLFLOW_TRACKING_URI"] = f"databricks://{args.profile}"
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

    from databricks.ai_search.client import AISearchClient
    from databricks.ai_search.reranker import DatabricksReranker
    from databricks.sdk.core import Config

    config = Config(profile=args.profile)
    authorization = config.authenticate().get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise RuntimeError("The selected Databricks profile did not return a bearer token")
    # AISearchClient's local-mode validator accepts the OAuth bearer value via
    # its token field.  The token remains in memory and is never printed.
    client = AISearchClient(
        workspace_url=config.host,
        personal_access_token=authorization.removeprefix("Bearer "),
        disable_notice=True,
    )
    index = client.get_index(endpoint_name=ENDPOINT_NAME, index_name=INDEX_NAME)

    checks = [
        {
            "name": "ann",
            "query_text": "2024年式プリウスのE-Fourの型式",
            "query_type": "ANN",
            "filters": None,
            "reranker": None,
            "expected_top_candidates": [
                ("9ea03add-de22-4c9a-8cac-c8de8e234d6a", 2),
            ],
        },
        {
            "name": "hybrid",
            "query_text": "2024年式プリウスのE-Fourの型式",
            "query_type": "HYBRID",
            "filters": None,
            "reranker": None,
            "expected_top_candidates": [
                ("9ea03add-de22-4c9a-8cac-c8de8e234d6a", 2),
            ],
        },
        {
            "name": "metadata_filter",
            "query_text": "PDA 作動速度 10-60 km/h",
            "query_type": "HYBRID",
            "filters": {"model": "Prius", "model_year": 2024},
            "reranker": None,
            "expected_top_candidates": [
                ("9ea03add-de22-4c9a-8cac-c8de8e234d6a", 3),
                ("1a6b0abc-e220-4dd5-85ba-1a6a07baee67", 3),
            ],
        },
        {
            "name": "reranker",
            "query_text": "2024年式プリウス Gグレード SEA 設定なし",
            "query_type": "HYBRID",
            "filters": {
                "model": "Prius",
                "model_year": 2024,
                "document_type": "equipment_spec",
            },
            "reranker": DatabricksReranker(
                columns_to_rerank=["title", "chunk_to_retrieve"]
            ),
            "expected_top_candidates": [
                ("00d0cbab-5813-45ea-a4e9-775e9056ad58", 2),
            ],
        },
    ]

    summaries = []
    for check in checks:
        raw = index.similarity_search(
            columns=COLUMNS,
            query_text=check["query_text"],
            query_type=check["query_type"],
            filters=check["filters"],
            num_results=5,
            debug_level=1,
            reranker=check["reranker"],
            disable_notice=True,
        )
        rows, debug = rows_from_response(raw)
        if not rows:
            raise AssertionError(f"{check['name']}: no search results")
        if any(str(row.get("project_id")) != PROJECT_ID for row in rows):
            raise AssertionError(f"{check['name']}: cross-Project result")
        if check["filters"] and any(
            row.get(key) != value for row in rows for key, value in check["filters"].items()
        ):
            raise AssertionError(f"{check['name']}: metadata filter mismatch")
        top = rows[0]
        top_key = (str(top.get("document_id")), int(top.get("page_number")))
        if top_key not in check["expected_top_candidates"]:
            raise AssertionError(
                f"{check['name']}: unexpected top result "
                f"{top.get('document_id')} page {top.get('page_number')}"
            )
        warnings = debug.get("warnings") if isinstance(debug, dict) else None
        if check["reranker"] is not None and warnings:
            raise AssertionError(f"reranker warning: {warnings}")
        summaries.append(
            {
                "check": check["name"],
                "row_count": len(rows),
                "top_document_id": top["document_id"],
                "top_page": top["page_number"],
                "top_score": top.get("score"),
                "reranker_time": debug.get("reranker_time") if isinstance(debug, dict) else None,
                "warnings": warnings,
            }
        )
    print(json.dumps({"status": "SUCCEEDED", "checks": summaries}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
