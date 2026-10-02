"""Shared test helpers: a tiny project whose check fails until `add` is fixed, and stubbed roles (no model)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

from local_memory_lab.knowledge.embedding import EmbeddingUnavailable
from local_memory_lab.team.roles import RoleReply


class _NoModelEmbedder:
    """Replaces the default embedder a session builds when a test passes none (keyword search only)."""

    model = "blocked-in-tests"

    def __init__(self, *args, **kwargs):
        pass

    def embed(self, text):
        raise EmbeddingUnavailable("embedding is blocked in the default test suite")

    def embed_many(self, texts):
        raise EmbeddingUnavailable("embedding is blocked in the default test suite")


# The default suite must never reach a model: any test that imports these fixtures has Ollama blocked.
# Role calls fail loudly; a session's default embedder is unavailable, so indexing uses keywords only.
# Tests that pass their own embedder or point one at a fake server are unaffected.
patch("local_memory_lab.agent.ollama.urlopen",
      side_effect=AssertionError("the default test suite must never call a model")).start()
patch("local_memory_lab.knowledge.service.OllamaEmbedder", _NoModelEmbedder).start()

SUITE = ["python", "-B", "-m", "unittest", "discover", "-s", "tests"]
BROKEN_ADD = 'def add(a, b):\n    """Add two numbers."""\n    return a - b\n'
FIXED_ADD = 'def add(a, b):\n    """Add two numbers."""\n    return a + b\n'
PROJECT = {
    "calc.py": BROKEN_ADD,
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


def team_roles(builder_text: str | None = None, verdict: str = "pass", hold: threading.Event | None = None,
               calls: list | None = None):
    """Patch the team's role calls: the builder (and debugger) reply with `builder_text`, the reviewer with
    `verdict`. If `hold` is given, builder calls wait on it so a test can act while a model call runs."""
    def fake(config, system, payload, schema, validate=None):
        if calls is not None:
            calls.append(config.role)
        if "verdict" in schema.get("properties", {}):
            out = {"verdict": verdict, "reasons": ["checked"], "quote": "return a + b" if verdict == "fail" else ""}
            if validate:
                validate(out)
            return RoleReply(out, "", 5, 0.1, 50.0, 1.0)
        if hold is not None:
            hold.wait(5)
        return RoleReply({"replace_block": builder_text or FIXED_ADD, "notes": ""}, "", 5, 0.1, 50.0, 1.0)
    return patch("local_memory_lab.team.pipeline.call_role", side_effect=fake)


def pending_patch(session, job_id: str, timeout: float = 10.0):
    """Wait for the job's gated patch approval and return it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with session.lock:
            for item in session.state.approvals.pending():
                if item.job == job_id and item.kind == "patch":
                    return item
            job = session.state.jobs.records[job_id]
            if job.state in {"done", "failed", "cancelled", "rejected"}:
                raise AssertionError(f"job ended as {job.state} ({job.reason}) before a patch approval")
        time.sleep(0.02)
    raise AssertionError("no patch approval appeared")
