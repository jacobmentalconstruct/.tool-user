"""Opt-in role probe: builder/debugger budgets and a reviewer comparison on passing cards."""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from ..knowledge.embedding import OllamaEmbedder
from ..team.gate import gate_facts
from ..team.regions import definitions, find_region
from ..team.roles import (REVIEWER_SCHEMA, RoleOutputError, call_role, candidate_schema,
                          load_roles)
from ..team.steps import (BUILDER_SYSTEM, DEBUGGER_SYSTEM, REVIEWER_SYSTEM, build_card,
                          candidate_input, require_citation)
from .harness import CONTEXT_BUDGET, build_context
from .tasks import _run_check, archive_snapshot, load_tasks, prepare_punched_copy

PROBE_TASKS = 5
# Reviewer candidates that fit fully on the 16 GB GPU (qwen3.5:35b does not; see PLAN.md D4).
# Timeouts follow measured speeds: about 57 tokens/s for 9b; the 14b coder has no thinking mode.
REVIEWERS = {"qwen3.5:9b": {"timeout_s": 180.0, "think": True},
             "qwen2.5-coder:14b": {"timeout_s": 120.0, "think": False}}
EMBEDDER = lambda: OllamaEmbedder("nomic-embed-text", timeout=30)  # noqa: E731
MAX_INVALID_WITH_CONTEXT = 1
CHECK_TIMEOUT = 60
SUITE = ["python", "-B", "-m", "unittest", "discover", "-s", "tests"]
SUITE_TIMEOUT = 300
OK_CHECK = {"name": "task check", "status": "ok", "exit_code": 0}
OFFLOADED = 0.99
# Plain plants: a name like "probe" let reviewers excuse the line as test infrastructure.
SEEDS = ('print("debug output")',
         'open("debug.log", "a", encoding="utf-8").close()')


def seed_bad(source: str, symbol: str, seed: str) -> str:
    """Add one unrequested side effect as the definition's first statement."""
    node = next(node for name, node in definitions(ast.parse(source).body) if name == symbol)
    body = node.body
    first = body[1] if len(body) > 1 and isinstance(body[0], ast.Expr) and isinstance(
        getattr(body[0], "value", None), ast.Constant) else body[0]
    lines = source.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in lines[first.lineno - 1] else "\n"
    lines.insert(first.lineno - 1, " " * first.col_offset + seed + newline)
    return "".join(lines)


def _team_task(title: str, description: str, path: str, symbol: str) -> dict:
    return {"title": title, "description": description, "check": "task check", "order": 1,
            "target": {"path": path, "symbol": symbol, "new": False}, "files": [path]}


def _attempt(role: str, config, task_id: str, run: Callable[[], dict]) -> dict:
    row = {"role": role, "model": config.model, "task_id": task_id, "valid": False,
           "reason": None, "capHit": False}
    try:
        reply = run()
    except RoleOutputError as exc:
        row.update(exc.record)
        return row
    except (OSError, RuntimeError, TimeoutError) as exc:
        row.update({"reason": "transport_error", "error": str(exc)})
        return row
    row.update({"valid": True, "evalCount": reply.eval_count, "elapsed_s": round(reply.elapsed_s, 2),
                "tokens_per_s": round(reply.tokens_per_s, 2), "gpuFraction": reply.gpu_fraction,
                "output": reply.output, "thinkingExcerpt": reply.thinking[-2000:]})
    return row


def _score_candidate(row: dict, copy: Path, task: dict, punched: str, region: str) -> None:
    if not row["valid"]:
        return
    target = copy / task["target"]["path"]
    target.write_bytes(punched.replace(region, row["output"]["replace_block"], 1).encode("utf-8"))
    row["passed"] = _run_check(task, copy, CHECK_TIMEOUT).returncode == 0


def edit_cards(repo_root: Path, specs: list[dict], root: Path) -> list[dict]:
    """Real accepted edits from history (clean) and the same edits plus a planted line (bad).

    A card is kept only if the project's test suite passes on it, as production cards would.
    """
    cards = []
    for index, spec in enumerate(specs):
        commit, path, symbol = spec["commit"], spec["path"], spec["symbol"]
        snapshot = root / f"edit-{commit}"
        archive_snapshot(repo_root, commit, snapshot)
        before = find_region(subprocess.run(
            ["git", "show", f"{commit}^:{path}"], cwd=repo_root, capture_output=True, text=True,
            encoding="utf-8", check=True).stdout, symbol)
        after_source = (snapshot / path).read_text(encoding="utf-8")
        bad_source = seed_bad(after_source, symbol, SEEDS[index % len(SEEDS)])
        seeded = root / f"edit-{commit}-seeded"
        shutil.copytree(snapshot, seeded)
        (seeded / path).write_bytes(bad_source.encode("utf-8"))
        task = _team_task(f"edit-{commit}", spec["intent"], path, symbol)
        for expected, folder, source in (("pass", snapshot, after_source), ("fail", seeded, bad_source)):
            if subprocess.run(SUITE, cwd=folder, capture_output=True, timeout=SUITE_TIMEOUT).returncode == 0:
                cards.append({"kind": "edit", "task_id": task["title"], "task": task, "before": before,
                              "after": find_region(source, symbol), "expected": expected})
    return cards


def run_role_probe(repo_root: Path, task_dir: Path, *, output_dir: Path | None = None,
                   context_budget: int = CONTEXT_BUDGET, reviewer_only: bool = False,
                   on_progress: Callable[[str], None] = print) -> tuple[Path, dict]:
    """Pin the snapshots, probe with the configured budgets, and record the run."""
    roles = load_roles(repo_root / "roles.json")
    tasks = load_tasks(task_dir)[:PROBE_TASKS]
    git = lambda *args: subprocess.run(["git", *args], cwd=repo_root, capture_output=True,  # noqa: E731
                                       text=True, check=True).stdout.strip()
    source = {"source_commit": git("rev-parse", "HEAD"), "source_dirty": bool(git("status", "--porcelain"))}
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    output_dir = _raw_dir(repo_root, output_dir)
    specs = json.loads((repo_root / "bench" / "review_edits.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="lab-role-probe-") as temporary:
        root = Path(temporary)
        archive_snapshot(repo_root, tasks[0]["source"].split("@", 1)[1], root / "snapshot")
        extra = edit_cards(repo_root, specs, root)
        on_progress(f"Prepared {len(extra)} edit cards from {len(specs)} recorded edits.")
        rows = probe_roles(roles, tasks, root / "snapshot", root, extra_cards=extra,
                           context_budget=context_budget, reviewer_only=reviewer_only,
                           checkpoint=output_dir / f"roles-{run_id}.partial.json",
                           on_progress=on_progress)
    return record_probe(rows, roles, tasks, repo_root=repo_root, context_budget=context_budget,
                        source=source, run_id=run_id, output_dir=output_dir)


def probe_roles(roles: dict, tasks: list[dict], snapshot: Path, root: Path, *,
                extra_cards: list[dict] = (), context_budget: int = CONTEXT_BUDGET,
                reviewer_only: bool = False, checkpoint: Path | None = None,
                on_progress: Callable[[str], None] = print) -> list[dict]:
    """Run builder and debugger, then every reviewer model over creation and edit cards."""
    rows: list[dict] = []

    def keep(row: dict, message: str) -> None:
        rows.append(row)
        if checkpoint is not None:  # a stopped run keeps every reply so far
            checkpoint.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        on_progress(message)

    prepared, cards = [], list(extra_cards)
    for index, task in enumerate(tasks):
        symbol, path = task["target"]["function"], task["target"]["path"]
        punched, _ = prepare_punched_copy(snapshot, task, root / f"{task['id']}-indexed")
        region = find_region(punched, symbol)
        team_task = _team_task(task["id"], task["goal"], path, symbol)
        original = (snapshot / path).read_text(encoding="utf-8")
        bad = seed_bad(original, symbol, SEEDS[index % len(SEEDS)])
        seeded = root / f"{task['id']}-seeded"
        shutil.copytree(snapshot, seeded)
        (seeded / path).write_bytes(bad.encode("utf-8"))
        cards.append({"kind": "create", "task_id": task["id"], "task": team_task, "before": region,
                      "after": find_region(original, symbol), "expected": "pass"})
        if _run_check(task, seeded, CHECK_TIMEOUT).returncode == 0:
            cards.append({"kind": "create", "task_id": task["id"], "task": team_task, "before": region,
                          "after": find_region(bad, symbol), "expected": "fail"})
        if not reviewer_only:
            pack, _ = build_context(root / f"{task['id']}-indexed", task, root / "control" / task["id"],
                                    embedder=EMBEDDER(), budget=context_budget)
            failing = _run_check(task, root / f"{task['id']}-indexed", CHECK_TIMEOUT)
            prepared.append((task, team_task, punched, region, pack,
                             (failing.stdout + failing.stderr)[-4000:]))
    on_progress(f"Prepared {len(tasks)} tasks and {len(cards)} reviewer cards.")
    for role, system in (() if reviewer_only else (("builder", BUILDER_SYSTEM), ("debugger", DEBUGGER_SYSTEM))):
        config = roles[role]
        for task, team_task, punched, region, pack, failing in prepared:
            feedback = {"check_output": failing, "previous_attempt": region} if role == "debugger" else None
            inputs = candidate_input(team_task, punched, region, pack, feedback)
            row = _attempt(role, config, task["id"], lambda: call_role(
                config, system, inputs, candidate_schema(False)))
            copy = root / f"{task['id']}-{role}"
            shutil.copytree(snapshot, copy)
            _score_candidate(row, copy, task, punched, region)
            keep(row, f"{role} {task['id']}: valid={row['valid']} passed={row.get('passed')}")
    for model, settings in REVIEWERS.items():
        config = replace(roles["reviewer"], model=model, **settings)
        for item in cards:
            path = item["task"]["files"][0]
            facts, _ = gate_facts(item["task"], {path: (Path(), item["before"].encode(), item["after"].encode())})
            card = build_card(item["task"], item["before"], item["after"], OK_CHECK, facts)
            row = _attempt("reviewer", config, item["task_id"], lambda: call_role(
                config, REVIEWER_SYSTEM, {"card": card}, REVIEWER_SCHEMA, validate=require_citation(card)))
            row.update({"kind": item["kind"], "expected": item["expected"],
                        "correct": row["valid"] and row["output"]["verdict"] == item["expected"]})
            keep(row, f"reviewer {model} {item['kind']} {item['task_id']} expected {item['expected']}: "
                      f"correct={row['correct']} gpu={row.get('gpuFraction')}")
    return rows


def _raw_dir(repo_root: Path, output_dir: Path | None) -> Path:
    output_dir = (output_dir or Path(tempfile.gettempdir()) / "local-memory-lab-bench").resolve()
    if output_dir.is_relative_to(repo_root.resolve()):
        raise ValueError("raw probe replies must remain outside the repository")
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def record_probe(rows: list[dict], roles: dict, tasks: list[dict], *, repo_root: Path,
                 context_budget: int, source: dict, run_id: str | None = None,
                 output_dir: Path | None = None, record_dir: Path | None = None) -> tuple[Path, dict]:
    """Keep raw replies outside the repo and a trimmed summary under bench/probes."""
    summary = summarize(rows)
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    output_dir = _raw_dir(repo_root, output_dir)
    document = {"run_id": run_id, **source, "context_budget": context_budget,
                "tasks": [task["id"] for task in tasks], "reviewers": REVIEWERS,
                "roles": {name: vars(config) for name, config in roles.items()}, "summary": summary}
    raw = output_dir / f"roles-{run_id}.json"
    raw.write_text(json.dumps({**document, "rows": rows}, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    (output_dir / f"roles-{run_id}.partial.json").unlink(missing_ok=True)
    trimmed = [{key: value for key, value in row.items()
                if key not in {"output", "thinkingExcerpt", "answerExcerpt"}} for row in rows]
    probes = record_dir or repo_root / "bench" / "probes"
    probes.mkdir(parents=True, exist_ok=True)
    (probes / f"roles-{run_id}.json").write_text(json.dumps(
        {**document, "raw_file": raw.name, "rows": trimmed}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    return raw, summary


def summarize(rows: list[dict]) -> dict:
    """Per-role counts and timing; reviewer accuracy per model and card kind; GPU residency."""
    summary = {}
    for key in sorted({(row["role"], row["model"], row.get("kind", "")) for row in rows}):
        group = [row for row in rows if (row["role"], row["model"], row.get("kind", "")) == key]
        valid = [row for row in group if row["valid"]]
        fractions = [row["gpuFraction"] for row in group if row.get("gpuFraction") is not None]
        item = {"attempts": len(group), "invalid": len(group) - len(valid),
                "cap_hits": sum(row["capHit"] for row in group),
                "mean_elapsed_s": round(sum(row["elapsed_s"] for row in valid) / len(valid), 2) if valid else None,
                "mean_tokens_per_s": round(sum(row["tokens_per_s"] for row in valid) / len(valid), 2) if valid else None,
                "min_gpu_fraction": min(fractions) if fractions else None,
                "offloaded_calls": sum(fraction < OFFLOADED for fraction in fractions)}
        if key[0] == "reviewer":
            item["clean_passed"] = sum(row["correct"] for row in group if row["expected"] == "pass")
            item["clean_cards"] = sum(row["expected"] == "pass" for row in group)
            item["seeded_bad_caught"] = sum(row["correct"] for row in group if row["expected"] == "fail")
            item["seeded_bad_cards"] = sum(row["expected"] == "fail" for row in group)
        else:
            item["passed"] = sum(bool(row.get("passed")) for row in group)
        summary[":".join(part for part in key if part)] = item
    builder = [item for name, item in summary.items() if name.startswith("builder:")]
    summary["builder_within_threshold"] = (builder[0]["invalid"] <= MAX_INVALID_WITH_CONTEXT
                                           if builder else None)
    return summary
