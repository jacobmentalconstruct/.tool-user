"""Opt-in planner probe: does each candidate plan a bench goal onto its real target function?"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Callable
from uuid import uuid4

from ..knowledge.embedding import OllamaEmbedder
from ..team.plan import PlanError, planner_request, validate_plan
from ..team.roles import RoleOutputError, call_role, load_roles, planner_schema
from ..team.steps import PLANNER_SYSTEM
from .harness import build_context
from .roles_probe import OFFLOADED, SUITE, _raw_dir
from .tasks import archive_snapshot, load_tasks, prepare_punched_copy

# Candidates and per-model settings; the 35b cannot be fully GPU-resident here (PLAN.md §1),
# so it is measured once on the first few goals and flagged.
PLANNERS = {"qwen3.5:9b": {"think": True, "timeout_s": 300.0, "goals": None},
            "qwen2.5-coder:14b": {"think": False, "timeout_s": 120.0, "goals": None},
            "qwen3.5:35b": {"think": True, "timeout_s": 600.0, "goals": 5}}
EMBEDDER = lambda: OllamaEmbedder("nomic-embed-text", timeout=30)  # noqa: E731


def _prepare(snapshot: Path, root: Path, task: dict) -> tuple[Path, SimpleNamespace]:
    """A punched project copy with an allowlist, plus its context pack, as the planner would see it."""
    copy = root / task["id"]
    prepare_punched_copy(snapshot, task, copy)
    (copy / ".lab").mkdir()
    (copy / ".lab" / "allowlist.json").write_text(json.dumps({"commands": {"tests": SUITE}}), encoding="utf-8")
    pack, _ = build_context(copy, task, root / "control" / task["id"], embedder=EMBEDDER())
    return copy, SimpleNamespace(store=True, context_pack=lambda goal, budget: pack)


def plan_once(config, task: dict, copy: Path, knowledge) -> dict:
    """One planner call scored on validity and on hitting the task's gold function."""
    row = {"model": config.model, "task_id": task["id"], "valid": False, "reason": None,
           "hit": False, "first_task_hit": False, "tasks": 0}
    started = time.monotonic()
    try:
        project, checks, payload = planner_request(task["goal"], copy, knowledge)
        reply = call_role(config, PLANNER_SYSTEM, payload, planner_schema(list(checks)))
        row.update({"elapsed_s": round(reply.elapsed_s, 2), "gpuFraction": reply.gpu_fraction,
                    "trace": reply.trace, "evalCount": reply.eval_count, "output": reply.output})
        specs = validate_plan(reply.output, project, checks)
    except RoleOutputError as exc:
        row.update(exc.record)
    except PlanError as exc:
        row.update({"reason": exc.reason, "error": str(exc)})
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        row.update({"reason": "transport_error", "error": str(exc)})
    else:
        gold = (task["target"]["path"], task["target"]["function"])
        hits = [(spec["target"]["path"], spec["target"]["symbol"]) == gold and not spec["target"]["new"]
                for spec in specs]
        row.update({"valid": True, "tasks": len(specs), "hit": any(hits), "first_task_hit": hits[0],
                    "targets": [f"{spec['target']['path']}::{spec['target']['symbol']}" for spec in specs]})
    row.setdefault("elapsed_s", round(time.monotonic() - started, 2))
    return row


def run_planner_probe(repo_root: Path, task_dir: Path, *, output_dir: Path | None = None,
                      on_progress: Callable[[str], None] = print) -> tuple[Path, dict]:
    roles = load_roles(repo_root / "roles.json")
    tasks = load_tasks(task_dir)
    git = lambda *args: subprocess.run(["git", *args], cwd=repo_root, capture_output=True,  # noqa: E731
                                       text=True, check=True).stdout.strip()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    output_dir = _raw_dir(repo_root, output_dir)
    checkpoint = output_dir / f"planner-{run_id}.partial.json"
    rows: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="lab-planner-probe-") as temporary:
        root = Path(temporary)
        archive_snapshot(repo_root, tasks[0]["source"].split("@", 1)[1], root / "snapshot")
        prepared = [(task, *_prepare(root / "snapshot", root, task)) for task in tasks]
        on_progress(f"Prepared {len(prepared)} goals.")
        for model, settings in PLANNERS.items():
            config = replace(roles["planner"], model=model, think=settings["think"],
                             timeout_s=settings["timeout_s"])
            for task, copy, knowledge in prepared[:settings["goals"]]:
                row = plan_once(config, task, copy, knowledge)
                rows.append(row)
                checkpoint.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
                on_progress(f"planner {model} {task['id']}: valid={row['valid']} hit={row['hit']} "
                            f"reason={row['reason']} {row['elapsed_s']}s gpu={row.get('gpuFraction')}")
    summary = summarize(rows)
    document = {"run_id": run_id, "source_commit": git("rev-parse", "HEAD"),
                "source_dirty": bool(git("status", "--porcelain")), "planners": PLANNERS,
                "planner_role": vars(roles["planner"]), "summary": summary}
    raw = output_dir / f"planner-{run_id}.json"
    raw.write_text(json.dumps({**document, "rows": rows}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    checkpoint.unlink(missing_ok=True)
    trimmed = [{key: value for key, value in row.items()
                if key not in {"output", "thinkingExcerpt", "answerExcerpt"}} for row in rows]
    probes = repo_root / "bench" / "probes"
    probes.mkdir(parents=True, exist_ok=True)
    (probes / f"planner-{run_id}.json").write_text(json.dumps(
        {**document, "raw_file": raw.name, "rows": trimmed}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return raw, summary


def summarize(rows: list[dict]) -> dict:
    summary = {}
    for model in dict.fromkeys(row["model"] for row in rows):
        group = [row for row in rows if row["model"] == model]
        valid = [row for row in group if row["valid"]]
        fractions = [row["gpuFraction"] for row in group if row.get("gpuFraction") is not None]
        reasons = {}
        for row in group:
            if not row["valid"]:
                reasons[row["reason"]] = reasons.get(row["reason"], 0) + 1
        summary[model] = {"goals": len(group), "valid": len(valid), "invalid_by_reason": reasons,
                          "target_hit": sum(row["hit"] for row in group),
                          "first_task_hit": sum(row["first_task_hit"] for row in group),
                          "mean_tasks": round(sum(row["tasks"] for row in valid) / len(valid), 2) if valid else None,
                          "mean_elapsed_s": round(sum(row["elapsed_s"] for row in group) / len(group), 2),
                          "min_gpu_fraction": min(fractions) if fractions else None,
                          "offloaded_calls": sum(fraction < OFFLOADED for fraction in fractions)}
    return summary
