"""Command-line validation for the committed bench corpus."""

from __future__ import annotations

import argparse
from pathlib import Path

from .harness import validate_context_packs
from .tasks import load_tasks, validate_baselines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lab.py bench")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="validate benchmark task records")
    validate.add_argument("--baselines", action="store_true",
                          help="run every named test on pristine and punched copies")
    validate.add_argument("--contexts", action="store_true",
                          help="build packs from punched copies and check for answer leakage")
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[3]
    task_dir = repo_root / "bench" / "tasks"
    try:
        tasks = load_tasks(task_dir)
        print(f"{len(tasks)} task records: valid")
        failures = []
        if args.baselines:
            baseline_failures = validate_baselines(repo_root, task_dir)
            print(f"pristine/punched single-test checks: {len(tasks)} tasks; "
                  f"{len(baseline_failures)} failures")
            failures.extend(baseline_failures)
            for failure in baseline_failures:
                print(f"FAIL: {failure}")
        if args.contexts:
            context_failures = validate_context_packs(repo_root, task_dir)
            print(f"punched-copy context leak checks: {len(tasks)} tasks; "
                  f"{len(context_failures)} failures")
            failures.extend(context_failures)
            for failure in context_failures:
                print(f"FAIL: {failure}")
        return 1 if failures else 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Bench validation failed: {exc}")
        return 1
