"""The planning step: the planner runs before plan approval, and code validates every task."""

from __future__ import annotations

import ast
import json
import keyword
from pathlib import Path, PurePosixPath
from uuid import uuid4

from ..command_runner import CommandRunner
from ..workspace.paths import Workspace
from .regions import definitions, find_region
from .roles import RoleOutputError, call_role, load_roles, planner_schema
from .steps import PLANNER_SYSTEM

ALLOWLIST = ".lab/allowlist.json"
MAX_LISTED_FILES = 400
CONTEXT_BUDGET = 6000


class PlanError(ValueError):
    """A planner reply that parsed but describes tasks code cannot accept."""

    reason = "invalid_plan"


def validate_plan(output: dict, project: Workspace, checks: dict[str, tuple[str, ...]]) -> list[dict]:
    """Turn the planner's tasks into immutable specifications, or reject the whole plan."""
    specs, spellings, targets = [], {}, set()
    for order, item in enumerate(output["tasks"], 1):
        where = f"task {order}"
        target = item["target"]
        path, symbol, new = target["path"], target["symbol"].strip(), target["new"]
        if not item["title"].strip() or not item["description"].strip():
            raise PlanError(f"{where} needs a title and a description")
        if "\\" in path or path.startswith("./") or PurePosixPath(path).as_posix() != path:
            raise PlanError(f"{where}: {path!r} is not a normalized relative path")
        if path.casefold() == ALLOWLIST:
            raise PlanError(f"{where}: the allowlist file cannot be a task file")
        try:
            file = project.path(path)
        except ValueError as exc:
            raise PlanError(f"{where}: {path}: {exc}") from exc
        if spellings.setdefault(path.casefold(), path) != path:
            raise PlanError(f"{where}: {path} collides by case with {spellings[path.casefold()]}")
        if (path.casefold(), symbol) in targets:
            raise PlanError(f"{where} repeats the target {path} {symbol}".rstrip())
        targets.add((path.casefold(), symbol))
        if item["check"] not in checks:
            raise PlanError(f"{where}: {item['check']!r} is not an allowlisted check")
        _check_target(where, file, path, symbol, new)
        specs.append({"title": item["title"].strip(), "description": item["description"].strip(),
                      "target": {"path": path, "symbol": symbol, "new": new}, "files": [path],
                      "check": item["check"], "order": order})
    return specs


def _check_target(where: str, file: Path, path: str, symbol: str, new: bool) -> None:
    if new and not symbol:  # a new file
        if file.exists():
            raise PlanError(f"{where}: {path} already exists")
        if not file.parent.is_dir():
            raise PlanError(f"{where}: the folder for {path} does not exist")
        return
    if not file.is_file():
        raise PlanError(f"{where}: {path} does not exist")
    try:
        source = file.read_text(encoding="utf-8")
        names = {name for name, _node in definitions(ast.parse(source).body)}
    except (UnicodeError, SyntaxError) as exc:
        raise PlanError(f"{where}: {path} is not readable Python") from exc
    if not new:
        try:
            find_region(source, symbol)
        except ValueError as exc:
            raise PlanError(f"{where}: {exc}") from exc
    elif not path.endswith(".py") or not symbol.isidentifier() or keyword.iskeyword(symbol):
        raise PlanError(f"{where}: a new function needs a plain name in a Python file")
    elif symbol in names:
        raise PlanError(f"{where}: {symbol} is already defined in {path}")


def plan_detail(specs: list[dict], checks: dict[str, tuple[str, ...]]) -> str:
    """The plan approval text: every task with its target and its exact check command (D19)."""
    lines = []
    for spec in specs:
        target = spec["target"]
        kind = ("new file" if not target["symbol"] else f"new function {target['symbol']}") \
            if target["new"] else target["symbol"]
        lines += [f"Task {spec['order']}: {spec['title']}", f"  Target: {target['path']} :: {kind}",
                  f"  Check: {spec['check']} -> {json.dumps(list(checks[spec['check']]))} "
                  "(runs in the task workspace)", f"  {spec['description']}", ""]
    return "\n".join(lines).rstrip() + "\n"


def planner_request(goal: str, project_root: Path, knowledge) -> tuple[Workspace, dict, dict]:
    """The planner's input: the goal, the project's files, the live allowlist and a context pack."""
    project = Workspace(project_root)
    checks = CommandRunner(project_root).commands()  # the live allowlist, never a task copy
    files = [relative for relative, _path in project.files()][:MAX_LISTED_FILES]
    pack = knowledge.context_pack(goal, CONTEXT_BUDGET) if knowledge.store is not None else None
    return project, checks, {"goal": goal, "files": files, "context_pack": pack,
                             "checks": {name: list(argv) for name, argv in checks.items()}}


def plan_goal(goal: str, project_root: Path, knowledge, config) -> tuple[list[dict], str]:
    """Ask the planner for 1-5 tasks and validate them against the live project."""
    project, checks, payload = planner_request(goal, project_root, knowledge)
    reply = call_role(config, PLANNER_SYSTEM, payload, planner_schema(list(checks)))
    specs = validate_plan(reply.output, project, checks)
    return specs, plan_detail(specs, checks)


def plan_job(session, job_id: str, config=None) -> str | None:
    """Plan one queued job; record its tasks and request plan approval, or fail it visibly."""
    with session.lock:
        job = session.state.jobs.records[job_id]
        if job.state != "planning":
            return None
        goal, root = job.goal, session.project_root
    try:
        specs, detail = plan_goal(goal, root, session.knowledge, config or load_roles()["planner"])
    except (RoleOutputError, PlanError, ValueError, RuntimeError, OSError) as exc:
        reason = getattr(exc, "reason", "planner_error")
        with session.lock:
            if session.state.jobs.records[job_id].state == "planning":
                session._record("system", "error", {"display": {"speaker": "Error", "text": f"Planning failed: {exc}"},
                                                    "failure": getattr(exc, "record", {"reason": reason})}, job=job_id)
                session.transition_job(job_id, "failed", reason=f"{reason}: {exc}")
        return None
    with session.lock:
        if session.state.jobs.records[job_id].state != "planning":
            return None
        for spec in specs:
            task_id = str(uuid4())
            session._record("system", "task.state",
                            session.state.tasks.transition_data(task_id, "pending", spec=spec),
                            job=job_id, task=task_id)
        session.transition_job(job_id, "awaiting_plan_approval")
        return session.request_approval("plan", f"Approve plan: {len(specs)} task(s)", detail,
                                        actor="system", job=job_id)
