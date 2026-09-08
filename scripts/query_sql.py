#!/usr/bin/env python3
"""Run one read-only-oriented SQL statement and print its result as JSON.

The caller is responsible for supplying a SELECT/DESCRIBE statement.  This
small helper shares the same fail-closed Statement Execution path as the
deployment scripts and is used by the step verification commands.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from execute_sql_file import execute_statement


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--statement")
    source.add_argument("--sql-file", type=Path)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--catalog")
    parser.add_argument("--schema")
    args = parser.parse_args()

    statement = (
        args.statement
        if args.statement is not None
        else args.sql_file.read_text(encoding="utf-8")
    )
    response = execute_statement(
        statement,
        profile=args.profile,
        warehouse_id=args.warehouse_id,
        catalog=args.catalog,
        schema=args.schema,
    )
    state = response.get("status", {}).get("state", "UNKNOWN")
    payload = {
        "statement_id": response.get("statement_id"),
        "state": state,
        "schema": response.get("manifest", {}).get("schema", {}),
        "rows": response.get("result", {}).get("data_array", []),
        "error": response.get("status", {}).get("error"),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if state == "SUCCEEDED" else 1


if __name__ == "__main__":
    sys.exit(main())
