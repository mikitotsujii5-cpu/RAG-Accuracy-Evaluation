#!/usr/bin/env python3
"""Execute an idempotent Databricks SQL file one statement at a time.

The script stops at the first failed statement and prints a compact JSON line
for every statement. It deliberately calls the Databricks CLI so the selected
profile, workspace routing, and OAuth behavior are identical to manual steps.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELED", "CLOSED"}


def split_sql(text: str) -> list[str]:
    """Split SQL without treating comment text as executable syntax.

    The deployment files contain prose such as ``Delta's`` in ``--``
    comments.  A quote-aware splitter that does not understand comments can
    therefore remain inside a false string literal and merge two statements.
    This scanner preserves comments in the emitted statement while ignoring
    their quotes and semicolons for splitting purposes.
    """

    statements: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    line_comment = False
    block_comment = False
    has_executable_text = False
    index = 0
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""

        if line_comment:
            buffer.append(char)
            if char in {"\n", "\r"}:
                line_comment = False
            index += 1
            continue

        if block_comment:
            buffer.append(char)
            if char == "*" and next_char == "/":
                buffer.append(next_char)
                block_comment = False
                index += 2
            else:
                index += 1
            continue

        if quote is None and char == "-" and next_char == "-":
            buffer.extend((char, next_char))
            line_comment = True
            index += 2
            continue
        if quote is None and char == "/" and next_char == "*":
            buffer.extend((char, next_char))
            block_comment = True
            index += 2
            continue

        if quote is not None:
            buffer.append(char)
            if char == quote:
                # SQL escapes a quote by doubling it (for example ``'it''s'``).
                if next_char == quote:
                    buffer.append(next_char)
                    index += 2
                    continue
                quote = None
            elif char == "\\" and next_char:
                # Preserve dialect-specific backslash escapes without letting
                # the escaped quote terminate the current literal.
                buffer.append(next_char)
                index += 2
                continue
            index += 1
            continue

        if char in {"'", '"', "`"}:
            has_executable_text = True
            quote = char
            buffer.append(char)
            index += 1
            continue
        if char == ";":
            statement = "".join(buffer).strip()
            if statement and has_executable_text:
                statements.append(statement)
            buffer = []
            has_executable_text = False
            index += 1
            continue
        if not char.isspace():
            has_executable_text = True
        buffer.append(char)
        index += 1
    remainder = "".join(buffer).strip()
    if remainder and has_executable_text:
        statements.append(remainder)
    return statements


def cli_json(args: list[str]) -> dict:
    completed = subprocess.run(
        ["databricks", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Databricks CLI returned invalid JSON: {completed.stdout}") from exc


def execute_statement(
    statement: str,
    *,
    profile: str,
    warehouse_id: str,
    catalog: str | None,
    schema: str | None,
) -> dict:
    request: dict[str, object] = {
        "warehouse_id": warehouse_id,
        "statement": statement,
        "wait_timeout": "50s",
        "on_wait_timeout": "CONTINUE",
    }
    if catalog:
        request["catalog"] = catalog
    if schema:
        request["schema"] = schema
    response = cli_json(
        [
            "api",
            "post",
            "/api/2.0/sql/statements",
            "--profile",
            profile,
            "--json",
            json.dumps(request, ensure_ascii=False),
            "-o",
            "json",
        ]
    )
    statement_id = response.get("statement_id")
    state = response.get("status", {}).get("state")
    while statement_id and state not in TERMINAL_STATES:
        time.sleep(2)
        response = cli_json(
            [
                "api",
                "get",
                f"/api/2.0/sql/statements/{statement_id}",
                "--profile",
                profile,
                "-o",
                "json",
            ]
        )
        state = response.get("status", {}).get("state")
    return response


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sql_file", type=Path)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--catalog")
    parser.add_argument("--schema")
    args = parser.parse_args()

    statements = split_sql(args.sql_file.read_text(encoding="utf-8"))
    if not statements:
        raise SystemExit("SQL file contains no executable statements")

    for position, statement in enumerate(statements, start=1):
        response = execute_statement(
            statement,
            profile=args.profile,
            warehouse_id=args.warehouse_id,
            catalog=args.catalog,
            schema=args.schema,
        )
        state = response.get("status", {}).get("state", "UNKNOWN")
        summary = next(
            (line.strip() for line in statement.splitlines() if line.strip() and not line.lstrip().startswith("--")),
            "SQL",
        )
        print(
            json.dumps(
                {
                    "position": position,
                    "total": len(statements),
                    "statement": summary[:140],
                    "statement_id": response.get("statement_id"),
                    "state": state,
                    "error": response.get("status", {}).get("error"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if state != "SUCCEEDED":
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
