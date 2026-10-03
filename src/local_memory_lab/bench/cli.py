"""Command-line validation for the committed bench corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .draft_probe import run_draft_probe
from .harness import validate_context_packs
from .planner_probe import run_planner_probe
from .roles_probe import run_role_probe
from .runner import (record_run, run_benchmark, validate_recorded_result, validate_results)
from .team_probe import run_team_probe
from .tasks import load_tasks, validate_baselines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lab.py bench")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="validate benchmark task records")
    validate.add_argument("--baselines", action="store_true",
                          help="run every named test on pristine and punched copies")
    validate.add_argument("--contexts", action="store_true",
                          help="build packs from punched copies and check for answer leakage")
    validate.add_argument("--results", action="store_true",
                          help="validate all committed run records and the role configuration")
    run = commands.add_parser("run", help="run bounded local builder comparisons")
    run.add_argument("--confirm-gpu-free", action="store_true",
                     help="confirm the GPU is free and no local roles are working")
    run.add_argument("--resume", type=Path,
                     help="resume an incomplete raw results file outside the repository")
    probe = commands.add_parser("roles", help="probe role budgets and compare reviewer models")
    probe.add_argument("--confirm-gpu-free", action="store_true",
                       help="confirm the GPU is free and no local roles are working")
    probe.add_argument("--context-budget", type=int, default=6000,
                       help="context pack budget; trim once if the builder exceeds its threshold")
    probe.add_argument("--reviewer-only", action="store_true",
                       help="skip builder and debugger; compare reviewer models only")
    planner = commands.add_parser("planner", help="compare planner models on the bench goals")
    planner.add_argument("--confirm-gpu-free", action="store_true",
                         help="confirm the GPU is free and no local roles are working")
    team = commands.add_parser("team", help="one real-model goal through the whole team on a throwaway project")
    team.add_argument("--confirm-gpu-free", action="store_true",
                      help="confirm the GPU is free and no local roles are working")
    drafts = commands.add_parser("drafts", help="draft goals from short conversations, then plan the valid ones")
    drafts.add_argument("--confirm-gpu-free", action="store_true",
                        help="confirm the GPU is free and no local roles are working")
    record = commands.add_parser("record", help="validate and import a completed raw run")
    record.add_argument("result_file", type=Path)
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[3]
    task_dir = repo_root / "bench" / "tasks"
    try:
        tasks = load_tasks(task_dir)
        print(f"{len(tasks)} task records: valid")
        failures = []
        if args.command == "run":
            if not args.confirm_gpu_free:
                raise ValueError("confirm the GPU is free and no local roles are working before running")
            output = run_benchmark(repo_root, task_dir, resume_path=args.resume,
                                   on_progress=lambda message: print(message, flush=True))
            print(f"Raw results checkpointed at {output}")
            print("Use `python lab.py bench record <path>` after the run completes to validate and import it.")
            return 0
        if args.command == "roles":
            if not args.confirm_gpu_free:
                raise ValueError("confirm the GPU is free and no local roles are working before running")
            raw, summary = run_role_probe(repo_root, task_dir, context_budget=args.context_budget,
                                          reviewer_only=args.reviewer_only,
                                          on_progress=lambda message: print(message, flush=True))
            print(json.dumps(summary, indent=2))
            print(f"Raw replies, including thinking text: {raw}")
            for name, item in summary.items():
                if isinstance(item, dict) and item["offloaded_calls"]:
                    print(f"WARNING: {name} ran partly on the CPU in {item['offloaded_calls']} calls "
                          f"(lowest GPU share {item['min_gpu_fraction']}); its timings are not comparable.")
            return 1 if summary["builder_within_threshold"] is False else 0
        if args.command == "planner":
            if not args.confirm_gpu_free:
                raise ValueError("confirm the GPU is free and no local roles are working before running")
            raw, summary = run_planner_probe(repo_root, task_dir,
                                             on_progress=lambda message: print(message, flush=True))
            print(json.dumps(summary, indent=2))
            print(f"Raw replies, including thinking text: {raw}")
            return 0
        if args.command == "team":
            if not args.confirm_gpu_free:
                raise ValueError("confirm the GPU is free and no local roles are working before running")
            raw, summary = run_team_probe(repo_root, on_progress=lambda message: print(message, flush=True))
            print(json.dumps(summary, indent=2))
            print(f"Full record: {raw}")
            return 0 if summary.get("job") == "done" else 1
        if args.command == "drafts":
            if not args.confirm_gpu_free:
                raise ValueError("confirm the GPU is free and no local roles are working before running")
            output, summary = run_draft_probe(repo_root, on_progress=lambda message: print(message, flush=True))
            print(json.dumps(summary, indent=2))
            print(f"Recorded {output.relative_to(repo_root)}")
            return 0
        if args.command == "record":
            output, selected = record_run(repo_root, args.result_file, task_dir)
            print(f"Recorded {output.relative_to(repo_root)}; selected builder: {selected}")
            return 0
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
        if args.results:
            results_dir = repo_root / "bench" / "results"
            result_files = sorted(results_dir.glob("*.json")) if results_dir.exists() else []
            if not result_files:
                failures.append("no committed benchmark result files")
            for result_file in result_files:
                document = json.loads(result_file.read_text(encoding="utf-8"))
                failures.extend(f"{result_file.name}: {failure}"
                                for failure in validate_recorded_result(document, tasks))
            roles = repo_root / "roles.json"
            if not roles.exists():
                failures.append("roles.json is missing")
            else:
                role_data = json.loads(roles.read_text(encoding="utf-8"))
                measured = {document.get("selected_builder") for document in
                            (json.loads(path.read_text(encoding="utf-8")) for path in result_files)}
                if not isinstance(role_data.get("builder"), dict) or role_data["builder"].get(
                        "model") not in measured:
                    failures.append("roles.json builder must be selected from measured models")
            if not failures:
                print(f"committed run records: {len(result_files)} valid; roles.json builder is measured")
        return 1 if failures else 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Bench validation failed: {exc}")
        return 1
