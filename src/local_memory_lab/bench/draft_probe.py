"""Opt-in goal-draft probe (T8): draft goals from short conversations, then plan each valid draft.

A measurement with no threshold: how many drafts pass validation, how many valid drafts plan validly, whether
the plan's target agrees with the draft's validated target, the rejection reasons, and time per draft. The
project is an archive of the current commit; nothing is approved or applied.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from ..knowledge.service import KnowledgeService
from ..team.draft import draft_goal
from ..team.plan import PlanError, plan_goal
from ..team.roles import RoleOutputError, load_roles
from .planner_probe import EMBEDDER
from .roles_probe import SUITE
from .tasks import archive_snapshot, load_tasks

# G1-G3 (T7) and five bench goals spread over different files; each conversation names its function.
CASES = [
    ("G1", "USER: The reviewer's fail counts even when its quote has a part like 'a'.\nASSISTANT: require_citation "
           "in team/steps.py checks only the total length of the quote parts against CITE_MIN.\nUSER: Make every "
           "part meet CITE_MIN.", ("src/local_memory_lab/team/steps.py", "existing_symbol", "require_citation")),
    ("G2", "USER: Our .gitignore rules miss Notes.TXT when the file is notes.txt.\nASSISTANT: excluded in "
           "workspace/paths.py matches rules with letter case, unlike git on Windows.\nUSER: Make excluded ignore "
           "letter case.", ("src/local_memory_lab/workspace/paths.py", "existing_symbol", "excluded")),
    ("G3", "USER: A crash can leave .lab-stage- and .lab-recover- files behind.\nASSISTANT: workspace/patching.py "
           "has no way to list them.\nUSER: Add a new function stale_temps(folder) to that existing file that "
           "returns their paths.", ("src/local_memory_lab/workspace/patching.py", "new_function", "stale_temps")),
]
BENCH = ("self-001", "self-006", "self-010", "self-020", "self-021")


def bench_cases(repo_root: Path) -> list[tuple]:
    cases = []
    for task in load_tasks(repo_root / "bench" / "tasks"):
        if task["id"] in BENCH:
            path, symbol = task["target"]["path"], task["target"]["function"]
            short = "/".join(path.split("/")[-2:])
            cases.append((task["id"], f"USER: {task['goal']}\nASSISTANT: That is {symbol} in {short}.\n"
                                      "USER: Turn that into a goal.", (path, "existing_symbol", symbol)))
    return cases


def run_draft_probe(repo_root: Path, *, on_progress: Callable[[str], None] = print, embedder=None,
                    probes_dir: Path | None = None) -> tuple[Path, dict]:
    git = lambda *args: subprocess.run(["git", *args], cwd=repo_root, capture_output=True,  # noqa: E731
                                       text=True, check=True).stdout.strip()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    config = load_roles(repo_root / "roles.json")["planner"]
    rows = []
    with tempfile.TemporaryDirectory(prefix="lab-draft-probe-", ignore_cleanup_errors=True) as temporary:
        project = Path(temporary) / "project"
        archive_snapshot(repo_root, "HEAD", project)
        (project / ".lab").mkdir()
        (project / ".lab" / "allowlist.json").write_text(json.dumps({"commands": {"tests": SUITE}}), encoding="utf-8")
        knowledge = KnowledgeService(project, control_root=Path(temporary) / "control",
                                     embedder=embedder or EMBEDDER(), start_worker=False)
        try:
            knowledge._index_project(knowledge.workspace, knowledge.store, True, set())
            for case_id, conversation, expected in CASES + bench_cases(repo_root):
                rows.append(_one(case_id, conversation, expected, project, knowledge, config))
                row = rows[-1]
                on_progress(f"{case_id}: valid={row['valid']} plan={row.get('planValid')} "
                            f"agrees={row.get('targetAgrees')} {row['seconds']}s {'; '.join(row['reasons'])[:120]}")
        finally:
            knowledge.close()
    valid = [row for row in rows if row["valid"]]
    summary = {"drafts": len(rows), "valid": len(valid),
               "planned_validly": sum(bool(row.get("planValid")) for row in valid),
               "plan_target_agrees": sum(bool(row.get("targetAgrees")) for row in valid),
               "draft_target_as_expected": sum(row["asExpected"] for row in valid),
               "rejection_reasons": [reason for row in rows for reason in row["reasons"]],
               "mean_seconds": round(sum(row["seconds"] for row in rows) / max(len(rows), 1), 1)}
    record = {"run_id": run_id, "source_commit": git("rev-parse", "HEAD"),
              "source_dirty": bool(git("status", "--porcelain")), "model": config.model,
              "settings": {**config.options(), "think": config.think}, "summary": summary, "rows": rows}
    probes = probes_dir or repo_root / "bench" / "probes"
    probes.mkdir(parents=True, exist_ok=True)
    output = probes / f"drafts-{run_id}.json"
    output.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output, summary


def _one(case_id: str, conversation: str, expected: tuple, project: Path, knowledge, config) -> dict:
    draft = draft_goal(conversation, project, knowledge, config)
    target = draft["target"] or {}
    row = {"case": case_id, "valid": draft["valid"], "goal": draft["goal"], "target": draft["target"],
           "reasons": draft["reasons"], "seconds": draft["seconds"], "loadSeconds": draft.get("loadSeconds"),
           "expected": dict(zip(("path", "shape", "symbol"), expected)),
           "asExpected": (target.get("path"), target.get("shape"), target.get("symbol")) == expected}
    if not draft["valid"]:
        return row
    new = target["shape"] != "existing_symbol"
    started = time.monotonic()
    try:
        specs, _detail = plan_goal(draft["goal"], project, knowledge, config)
    except (PlanError, RoleOutputError, TimeoutError, RuntimeError, OSError) as exc:
        return {**row, "planValid": False, "planReason": str(exc)[:300], "planSeconds": round(time.monotonic() - started, 1)}
    wanted = {"path": target["path"], "symbol": target["symbol"], "new": new}
    return {**row, "planValid": True, "planTargets": [spec["target"] for spec in specs],
            "targetAgrees": any(spec["target"] == wanted for spec in specs),
            "planSeconds": round(time.monotonic() - started, 1)}
