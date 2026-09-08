#!/usr/bin/env python3
"""Wait for an AI Search index and verify its indexed row count."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time


def get_index(profile: str, index_name: str) -> dict:
    completed = subprocess.run(
        [
            "databricks", "vector-search-indexes", "get-index", index_name,
            "-p", profile, "-o", "json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return json.loads(completed.stdout)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--index-name", required=True)
    parser.add_argument("--expected-rows", type=int, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    args = parser.parse_args()

    deadline = time.monotonic() + args.timeout_seconds
    previous: tuple[object, ...] | None = None
    while True:
        payload = get_index(args.profile, args.index_name)
        status = payload.get("status") or {}
        snapshot = (
            status.get("ready"),
            status.get("indexed_row_count"),
            status.get("message"),
        )
        if snapshot != previous:
            print(
                json.dumps(
                    {
                        "ready": snapshot[0],
                        "indexed_row_count": snapshot[1],
                        "message": snapshot[2],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            previous = snapshot
        message = str(status.get("message") or "")
        if "FAIL" in message.upper() or "ERROR" in message.upper():
            return 1
        if status.get("ready") is True:
            row_count = int(status.get("indexed_row_count") or 0)
            if row_count != args.expected_rows:
                print(
                    f"Indexed row mismatch: expected {args.expected_rows}, got {row_count}",
                    file=sys.stderr,
                )
                return 1
            expected_spec = payload.get("delta_sync_index_spec") or {}
            if payload.get("index_subtype") != "HYBRID":
                print("Index subtype is not HYBRID", file=sys.stderr)
                return 1
            if expected_spec.get("pipeline_type") != "TRIGGERED":
                print("Pipeline type is not TRIGGERED", file=sys.stderr)
                return 1
            return 0
        if time.monotonic() >= deadline:
            print("Timed out waiting for AI Search index", file=sys.stderr)
            return 1
        time.sleep(10)


if __name__ == "__main__":
    sys.exit(main())
