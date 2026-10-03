"""One task through the team inside its disposable workspace (D16): check first, build, debug, review, gate."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from ..command_runner import CommandRunner
from ..workspace.scratch import TaskWorkspace
from .candidate import region_of, updated_source
from .gate import GateResult, gate, gate_facts
from .plan import PlanError, check_target
from .roles import REVIEWER_SCHEMA, RoleOutputError, call_role, candidate_schema
from .steps import BUILDER_SYSTEM, DEBUGGER_SYSTEM, REVIEWER_SYSTEM, build_card, candidate_input, require_citation

MAX_DEBUG_ROUNDS = 2
CONTEXT_BUDGET = 6000
OUTPUT_TAIL = 4000


class TaskFailed(Exception):
    """A task ends as failed with a reason code (D17) and, for a role reply, its failure record."""

    def __init__(self, reason: str, message: str, failure: dict | None = None):
        super().__init__(message)
        self.reason = reason
        self.failure = failure or {"reason": reason, "error": message}


class TaskCancelled(Exception):
    """The USER cancelled the job; nothing more runs for this task."""


@dataclass
class TaskResult:
    workspace: TaskWorkspace
    gate: GateResult
    origin_role: str
    candidate: str


def role_reply(config, system: str, payload: dict, schema: dict, *, validate=None, call=None):
    """One role call; a cap is retried once without the context pack; failures become reason codes."""
    for attempt in (payload, {key: value for key, value in payload.items() if key != "context_pack"}):
        try:
            return (call or call_role)(config, system, attempt, schema, validate=validate)
        except RoleOutputError as exc:
            if exc.reason != "cap_exhausted" or "context_pack" not in attempt:
                raise TaskFailed(exc.reason, str(exc), exc.record) from exc
        except TimeoutError as exc:  # also socket timeouts (M1)
            raise TaskFailed("role_timeout", f"{config.role} call timed out after {config.timeout_s:g} s") from exc
        except (RuntimeError, OSError) as exc:
            raise TaskFailed("model_unavailable", f"{config.role} model unavailable: {exc}") from exc
    raise TaskFailed("cap_exhausted", "output cap reached again with a trimmed context")


def run_task(task, project_root: Path, scratch: Path, roles: dict, knowledge, *,
             step: Callable[..., None], cancelled: Callable[[], bool],
             on_command: Callable[[dict], None], call=None) -> TaskResult:
    """Run one recorded task to its gate. The selected project is never written here."""
    spec = task.spec
    target, path = spec["target"], spec["target"]["path"]
    workspace = TaskWorkspace.create(project_root, scratch, [path])
    try:
        command = CommandRunner(project_root).resolve(spec["check"])  # the live allowlist (D19)
        runner = CommandRunner(workspace.root)
        spec_here = replace(command, root=runner.root)

        def check() -> dict:
            if cancelled():
                raise TaskCancelled()
            result = runner.run(spec_here, cancelled=cancelled)
            on_command(result)
            if result["status"] == "cancelled":
                raise TaskCancelled()
            return result

        try:  # an earlier task's applied change may have moved or removed this target (reviewer L4)
            check_target(f"task {spec['order']}", workspace.path(path), path, target["symbol"], target["new"])
        except PlanError as exc:
            raise TaskFailed("invalid_plan", f"the plan no longer fits the project: {exc}") from exc
        step("building")
        if check()["status"] == "ok":
            raise TaskFailed("check_not_exercising",
                             f"check {spec['check']} already passes before any change, so it cannot test this task")
        before = workspace.before[path]
        source = before.decode("utf-8") if before is not None else ""
        region = region_of(source, target)
        pack = knowledge.context_pack(spec["description"], CONTEXT_BUDGET) if knowledge.store is not None else None
        new_file = target["new"] and not target["symbol"]
        field = "content" if new_file else "replace_block"
        schema = candidate_schema(new_file)
        text = role_reply(roles["builder"], BUILDER_SYSTEM, candidate_input(spec, source, region, pack),
                          schema, call=call).output[field]
        origin = "role:builder"
        for round_number in range(MAX_DEBUG_ROUNDS + 1):
            workspace.write(path, updated_source(source, target, text))
            step("testing")
            result = check()
            if result["status"] == "ok":
                break
            if round_number == MAX_DEBUG_ROUNDS:
                raise TaskFailed("debug_exhausted", f"check still fails after {MAX_DEBUG_ROUNDS} debug rounds")
            step("debugging")
            feedback = {"check_output": result["output"][-OUTPUT_TAIL:], "previous_attempt": text}
            text = role_reply(roles["debugger"], DEBUGGER_SYSTEM,
                              candidate_input(spec, source, region, pack, feedback), schema, call=call).output[field]
            origin = "role:debugger"
        if cancelled():
            raise TaskCancelled()
        step("reviewing")
        changes = workspace.changes()
        facts, _diff = gate_facts(spec, changes)
        summary = {"name": spec["check"], "status": result["status"], "exit_code": result["exit_code"]}
        card = build_card(spec, region, text, summary, facts)
        verdict = role_reply(roles["reviewer"], REVIEWER_SYSTEM, {"card": card}, REVIEWER_SCHEMA,
                             validate=require_citation(card), call=call).output
        outcome = gate(spec, changes, result, verdict["verdict"])
        if not outcome.passed:
            detail = "; ".join(verdict["reasons"]) if outcome.reason == "review_failed" else outcome.reason
            raise TaskFailed(outcome.reason, detail, {"reason": outcome.reason, "error": detail,
                                                      "quote": verdict.get("quote", ""), "facts": outcome.facts})
        step("gated")
        return TaskResult(workspace, outcome, origin, hashlib.sha256(text.encode("utf-8")).hexdigest()[:12])
    except BaseException:
        workspace.discard()
        raise
