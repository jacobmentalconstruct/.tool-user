"""The deterministic gate between a reviewed candidate and the USER's patch approval."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..workspace.patching import MAX_DIFF, MAX_FILES, unified_diff


@dataclass
class GateResult:
    passed: bool
    reason: str | None
    facts: dict
    diff: str


def gate_facts(task: dict, changes: dict[str, tuple[Path, bytes | None, bytes | None]]) -> tuple[dict, str]:
    """Path and size facts, computed before review so the reviewer card can show them."""
    allowed = set(task["files"])
    paths_ok = bool(changes) and all(
        relative in allowed and after is not None and (before is not None or task["target"]["new"])
        for relative, (_path, before, after) in changes.items())
    diff = "\n".join(unified_diff(relative, before or b"", after or b"").rstrip("\n")
                     for relative, (_path, before, after) in changes.items())
    return {"paths_ok": paths_ok, "files": sorted(changes), "file_count": len(changes),
            "diff_chars": len(diff)}, diff


def gate(task: dict, changes: dict, check: dict, verdict: str) -> GateResult:
    """Pass only with the check at exit 0, a reviewer pass, task-file paths and patch caps (D16)."""
    facts, diff = gate_facts(task, changes)
    if not facts["paths_ok"]:
        reason = "path_outside_task"
    elif check.get("status") != "ok" or check.get("exit_code") != 0:
        reason = "check_failed"
    elif verdict != "pass":
        reason = "review_failed"
    elif facts["file_count"] > MAX_FILES or facts["diff_chars"] > MAX_DIFF:
        reason = "patch_too_large"
    else:
        reason = None
    return GateResult(reason is None, reason, facts, diff)
