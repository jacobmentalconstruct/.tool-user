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
# Timeouts follow the first probe: about 57 tokens/s for 9b and 11 for 35b at the reviewer budget.
REVIEWER_MODELS = {"qwen3.5:9b": 180, "qwen3.5:35b": 900}
EMBEDDER = lambda: OllamaEmbedder("nomic-embed-text", timeout=30)  # noqa: E731
MAX_INVALID_WITH_CONTEXT = 1
CHECK_TIMEOUT = 60
SEEDS = ('print("lab-probe: debug output")',
         'open("lab-probe-marker.txt", "a", encoding="utf-8").close()')


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


def _team_task(task: dict) -> dict:
    target = task["target"]
    return {"title": task["id"], "description": task["goal"], "check": "task check", "order": 1,
            "target": {"path": target["path"], "symbol": target["function"], "new": False},
            "files": [target["path"]]}


def _attempt(role: str, config, task: dict, run: Callable[[], dict]) -> dict:
    row = {"role": role, "model": config.model, "task_id": task["id"], "valid": False,
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
                "tokens_per_s": round(reply.tokens_per_s, 2), "output": reply.output,
                "thinkingExcerpt": reply.thinking[-2000:]})
    return row


def _score_candidate(row: dict, copy: Path, task: dict, punched: str, region: str) -> None:
    if not row["valid"]:
        return
    target = copy / task["target"]["path"]
    target.write_bytes(punched.replace(region, row["output"]["replace_block"], 1).encode("utf-8"))
    row["passed"] = _run_check(task, copy, CHECK_TIMEOUT).returncode == 0


def run_role_probe(repo_root: Path, task_dir: Path, *, output_dir: Path | None = None,
                   context_budget: int = CONTEXT_BUDGET,
                   on_progress: Callable[[str], None] = print) -> tuple[Path, dict]:
    """Pin the corpus snapshot, probe with the configured budgets, and record the run."""
    roles = load_roles(repo_root / "roles.json")
    tasks = load_tasks(task_dir)[:PROBE_TASKS]
    git = lambda *args: subprocess.run(["git", *args], cwd=repo_root, capture_output=True,  # noqa: E731
                                       text=True, check=True).stdout.strip()
    source = {"source_commit": git("rev-parse", "HEAD"), "source_dirty": bool(git("status", "--porcelain"))}
    with tempfile.TemporaryDirectory(prefix="lab-role-probe-") as temporary:
        root = Path(temporary)
        archive_snapshot(repo_root, tasks[0]["source"].split("@", 1)[1], root / "snapshot")
        rows = probe_roles(roles, tasks, root / "snapshot", root, context_budget=context_budget,
                           on_progress=on_progress)
    return record_probe(rows, roles, tasks, repo_root=repo_root, context_budget=context_budget,
                        source=source, output_dir=output_dir)


def probe_roles(roles: dict, tasks: list[dict], snapshot: Path, root: Path, *,
                context_budget: int = CONTEXT_BUDGET,
                on_progress: Callable[[str], None] = print) -> list[dict]:
    """Run builder, debugger and both reviewer models over tasks from one prepared snapshot."""
    rows: list[dict] = []
    prepared = []
    for index, task in enumerate(tasks):
        symbol, path = task["target"]["function"], task["target"]["path"]
        punched, _ = prepare_punched_copy(snapshot, task, root / f"{task['id']}-indexed")
        pack, _ = build_context(root / f"{task['id']}-indexed", task, root / "control" / task["id"],
                                embedder=EMBEDDER(), budget=context_budget)
        original = (snapshot / path).read_text(encoding="utf-8")
        failing = _run_check(task, root / f"{task['id']}-indexed", CHECK_TIMEOUT)
        cards = [("pass", find_region(original, symbol))]
        bad = seed_bad(original, symbol, SEEDS[index % len(SEEDS)])
        seeded = root / f"{task['id']}-seeded"
        shutil.copytree(snapshot, seeded)
        (seeded / path).write_bytes(bad.encode("utf-8"))
        if _run_check(task, seeded, CHECK_TIMEOUT).returncode == 0:
            cards.append(("fail", find_region(bad, symbol)))
        prepared.append((task, punched, find_region(punched, symbol), pack,
                         (failing.stdout + failing.stderr)[-4000:], cards))
    on_progress(f"Prepared {len(prepared)} tasks; probing builder and debugger.")
    for role, system in (("builder", BUILDER_SYSTEM), ("debugger", DEBUGGER_SYSTEM)):
        config = roles[role]
        for task, punched, region, pack, failing, _cards in prepared:
            feedback = {"check_output": failing, "previous_attempt": region} if role == "debugger" else None
            inputs = candidate_input(_team_task(task), punched, region, pack, feedback)
            row = _attempt(role, config, task, lambda: call_role(
                config, system, inputs, candidate_schema(False)))
            copy = root / f"{task['id']}-{role}"
            shutil.copytree(snapshot, copy)
            _score_candidate(row, copy, task, punched, region)
            rows.append(row)
            on_progress(f"{role} {task['id']}: valid={row['valid']} passed={row.get('passed')}")
    for model, timeout in REVIEWER_MODELS.items():
        config = replace(roles["reviewer"], model=model, timeout_s=float(timeout))
        for task, punched, region, _pack, _failing, cards in prepared:
            team_task = _team_task(task)
            for expected, after in cards:
                changes = {team_task["files"][0]: (Path(), region.encode(), after.encode())}
                facts, _ = gate_facts(team_task, changes)
                card = build_card(team_task, region, after,
                                  {"name": "task check", "status": "ok", "exit_code": 0}, facts)
                row = _attempt("reviewer", config, task, lambda: call_role(
                    config, REVIEWER_SYSTEM, {"card": card}, REVIEWER_SCHEMA,
                    validate=require_citation(card)))
                row["expected"] = expected
                row["correct"] = row["valid"] and row["output"]["verdict"] == expected
                rows.append(row)
                on_progress(f"reviewer {model} {task['id']} expected {expected}: "
                            f"correct={row['correct']}")
    return rows


def record_probe(rows: list[dict], roles: dict, tasks: list[dict], *, repo_root: Path,
                 context_budget: int, source: dict, output_dir: Path | None = None,
                 record_dir: Path | None = None) -> tuple[Path, dict]:
    """Keep raw replies outside the repo and a trimmed summary under bench/probes."""
    summary = summarize(rows)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    output_dir = (output_dir or Path(tempfile.gettempdir()) / "local-memory-lab-bench").resolve()
    if output_dir.is_relative_to(repo_root.resolve()):
        raise ValueError("raw probe replies must remain outside the repository")
    output_dir.mkdir(parents=True, exist_ok=True)
    document = {"run_id": run_id, **source, "context_budget": context_budget,
                "tasks": [task["id"] for task in tasks],
                "roles": {name: vars(config) for name, config in roles.items()}, "summary": summary}
    raw = output_dir / f"roles-{run_id}.json"
    raw.write_text(json.dumps({**document, "rows": rows}, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    trimmed = [{key: value for key, value in row.items()
                if key not in {"output", "thinkingExcerpt", "answerExcerpt"}} for row in rows]
    probes = record_dir or repo_root / "bench" / "probes"
    probes.mkdir(parents=True, exist_ok=True)
    (probes / f"roles-{run_id}.json").write_text(json.dumps(
        {**document, "raw_file": raw.name, "rows": trimmed}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    return raw, summary


def summarize(rows: list[dict]) -> dict:
    """Per-role invalid, cap and pass counts, timing, and reviewer accuracy per model."""
    summary = {}
    for key in sorted({(row["role"], row["model"]) for row in rows}):
        group = [row for row in rows if (row["role"], row["model"]) == key]
        valid = [row for row in group if row["valid"]]
        item = {"attempts": len(group), "invalid": len(group) - len(valid),
                "cap_hits": sum(row["capHit"] for row in group),
                "mean_elapsed_s": round(sum(row["elapsed_s"] for row in valid) / len(valid), 2) if valid else None,
                "mean_tokens_per_s": round(sum(row["tokens_per_s"] for row in valid) / len(valid), 2) if valid else None}
        if key[0] == "reviewer":
            item["correct"] = sum(row["correct"] for row in group)
            item["seeded_bad_caught"] = sum(row["correct"] for row in group if row["expected"] == "fail")
            item["seeded_bad_cards"] = sum(row["expected"] == "fail" for row in group)
        else:
            item["passed"] = sum(bool(row.get("passed")) for row in group)
        summary[f"{key[0]}:{key[1]}"] = item
    builder = [item for name, item in summary.items() if name.startswith("builder:")]
    summary["builder_within_threshold"] = bool(builder) and builder[0]["invalid"] <= MAX_INVALID_WITH_CONTEXT
    return summary
