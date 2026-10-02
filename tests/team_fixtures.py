"""Shared test helpers: a tiny project with an allowlist, and a stubbed planner (no model)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

from local_memory_lab.team.roles import RoleReply

SUITE = ["python", "-B", "-m", "unittest", "discover", "-s", "tests"]
PROJECT = {
    "calc.py": 'def add(a, b):\n    """Add two numbers."""\n    return a + b\n',
    "tests/test_calc.py": ("import sys\nimport unittest\nfrom pathlib import Path\n\n"
                           "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
                           "from calc import add  # noqa: E402\n\n\n"
                           "class CalcTests(unittest.TestCase):\n"
                           "    def test_add(self):\n        self.assertEqual(5, add(2, 3))\n"),
    ".lab/allowlist.json": json.dumps({"commands": {"tests": SUITE}}),
}
ONE_TASK = {"tasks": [{"title": "Fix add", "description": "Return the sum of two numbers.",
                       "target": {"path": "calc.py", "symbol": "add", "new": False}, "check": "tests"}]}


def make_project(folder: Path) -> Path:
    for relative, text in PROJECT.items():
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    return folder


def planner(output: dict = ONE_TASK, error: Exception | None = None,
            target: str = "local_memory_lab.team.plan.call_role"):
    """Patch the planner's role call; every call returns `output` or raises `error`."""
    def fake(config, system, payload, schema, **kwargs):
        if error is not None:
            raise error
        return RoleReply(output, "", 10, 0.1, 100.0, 1.0)
    return patch(target, side_effect=fake)


def wait_for(session, job_id: str, state: str, timeout: float = 3.0) -> None:
    """Wait for a job state; for awaiting_plan_approval, also for its pending approval."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with session.lock:
            job = session.state.jobs.records.get(job_id)
            ready = job is not None and job.state == state and (
                state != "awaiting_plan_approval" or
                any(item.job == job_id for item in session.state.approvals.pending()))
        if ready:
            return
        time.sleep(0.01)
    job = session.state.jobs.records.get(job_id)
    raise AssertionError(f"job did not reach {state}; it is {job.state if job else None} "
                         f"({job.reason if job else ''})")
