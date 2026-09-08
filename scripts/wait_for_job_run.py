#!/usr/bin/env python3
"""Wait for one Lakeflow Job run and fail closed on non-success states."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time


TERMINAL = {"TERMINATED", "SKIPPED", "INTERNAL_ERROR", "BLOCKED"}


def get_run(profile: str, run_id: int) -> dict:
    completed = subprocess.run(
        ["databricks", "jobs", "get-run", str(run_id), "-p", profile, "-o", "json"],
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
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    args = parser.parse_args()

    deadline = time.monotonic() + args.timeout_seconds
    previous: tuple[str, str, str] | None = None
    while True:
        payload = get_run(args.profile, args.run_id)
        state = payload.get("state") or {}
        snapshot = (
            str(state.get("life_cycle_state") or "UNKNOWN"),
            str(state.get("result_state") or ""),
            str(state.get("state_message") or ""),
        )
        if snapshot != previous:
            print(
                json.dumps(
                    {
                        "run_id": args.run_id,
                        "life_cycle_state": snapshot[0],
                        "result_state": snapshot[1] or None,
                        "message": snapshot[2] or None,
                        "run_page_url": payload.get("run_page_url"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            previous = snapshot
        if snapshot[0] in TERMINAL:
            return 0 if snapshot[0] == "TERMINATED" and snapshot[1] == "SUCCESS" else 1
        if time.monotonic() >= deadline:
            print("Timed out waiting for Lakeflow Job run", file=sys.stderr)
            return 1
        time.sleep(10)


if __name__ == "__main__":
    sys.exit(main())
