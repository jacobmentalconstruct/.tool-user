"""Opt-in real-model run (T6): one deliberately boring goal through the whole team on a throwaway project.

The probe stands in for the USER only on the throwaway project it creates: it approves the plan and the
gated patch, and records that it did. Every role call is timed and traced; raw output stays outside git.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from ..locations import CONTROL
from ..team import pipeline, plan
from ..team.roles import call_role

GOAL = "Implement restock in inventory.py so stock levels can be increased."
PROJECT = {
    "inventory.py": ('def restock(levels: dict, item: str, amount: int) -> int:\n'
                     '    """Add amount to levels[item] and return the new level."""\n'
                     '    raise NotImplementedError\n'),
    # Data-driven tests: the rule for amounts below 1 lives only in cases.json, which is not indexed, so
    # the goal and the context pack do not reveal it. The first build most likely fails its check and the
    # debugger runs on the failing output (a first run with the rule in the test code needed no debugger).
    "tests/test_inventory.py": (
        "import json\nimport sys\nimport unittest\nfrom pathlib import Path\n\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
        "from inventory import restock  # noqa: E402\n\n"
        "CASES = json.loads((Path(__file__).parent / 'cases.json').read_text(encoding='utf-8'))\n\n\n"
        "class RestockTests(unittest.TestCase):\n"
        "    def test_cases(self):\n"
        "        for case in CASES:\n"
        "            with self.subTest(case=case['name']):\n"
        "                levels = dict(case['levels'])\n"
        "                if 'error' in case:\n"
        "                    with self.assertRaisesRegex(ValueError, case['error']):\n"
        "                        restock(levels, case['item'], case['amount'])\n"
        "                else:\n"
        "                    self.assertEqual(case['expected'], restock(levels, case['item'], case['amount']))\n"
        "                    self.assertEqual(case['expected'], levels[case['item']])\n"),
    "tests/cases.json": json.dumps([
        {"name": "adds to an existing item", "levels": {"apples": 3}, "item": "apples", "amount": 2, "expected": 5},
        {"name": "new items start from zero", "levels": {}, "item": "pears", "amount": 4, "expected": 4},
        {"name": "rejects amounts below one", "levels": {}, "item": "plums", "amount": 0,
         "error": "amount must be at least 1"}], indent=2),
    ".lab/allowlist.json": json.dumps({"commands": {"tests": ["python", "-B", "-m", "unittest", "discover",
                                                              "-s", "tests"]}}),
}


def fingerprint(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file() and "__pycache__" not in path.parts}


def run_team_probe(repo_root: Path, *, on_progress: Callable[[str], None] = print,
                   timeout: float = 1800.0) -> tuple[Path, dict]:
    from ..session import SharedSession  # imported here: the bench reaches the session only in this probe

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    base = CONTROL / "work" / f"team-{run_id}"
    project = base / "project"
    for relative, text in PROJECT.items():
        (project / relative).parent.mkdir(parents=True, exist_ok=True)
        (project / relative).write_bytes(text.encode("utf-8"))
    calls: list[dict] = []

    def timed():
        def call(config, system, payload, schema, validate=None):
            started = time.monotonic()
            row = {"role": config.role, "model": config.model, "startedAt": round(started - began, 1)}
            try:
                reply = call_role(config, system, payload, schema, validate=validate)
            except Exception as exc:
                row.update({"ok": False, "seconds": round(time.monotonic() - started, 1),
                            "error": str(exc)[:300], "trace": getattr(exc, "record", {}).get("trace")})
                calls.append(row)
                raise
            row.update({"ok": True, "seconds": round(time.monotonic() - started, 1), "output": reply.output,
                        "trace": reply.trace, "gpuFraction": reply.gpu_fraction})
            calls.append(row)
            on_progress(f"{config.role} ({config.model}) answered in {row['seconds']} s, "
                        f"load {reply.trace.get('loadSeconds')} s, gpu {reply.gpu_fraction}")
            return reply
        return call

    began = time.monotonic()
    original_plan, original_pipeline = plan.call_role, pipeline.call_role
    plan.call_role, pipeline.call_role = timed(), timed()
    session = SharedSession(base / "events.sqlite", load_models=False, start_worker=True)
    result = {"run_id": run_id, "goal": GOAL, "approvals_by": "probe, standing in for the USER on a throwaway project"}
    try:
        session.set_project_root(str(project))
        job_id = session.submit_goal(GOAL, "AGENT")
        deadline = time.monotonic() + timeout

        def wait(predicate, what):
            while time.monotonic() < deadline:
                with session.lock:
                    value = predicate()
                    job = session.state.jobs.records[job_id]
                if value:
                    return value
                if job.state in {"done", "failed", "cancelled", "rejected"}:
                    raise RuntimeError(f"job ended as {job.state} ({job.reason}) while waiting for {what}")
                time.sleep(0.5)
            raise TimeoutError(f"timed out waiting for {what}")

        pending = lambda kind: next((item for item in session.state.approvals.pending()  # noqa: E731
                                     if item.job == job_id and item.kind == kind), None)
        plan_approval = wait(lambda: pending("plan"), "the plan approval")
        result["plan"] = plan_approval.detail
        on_progress("Plan:\n" + plan_approval.detail)
        session.approve(plan_approval.id, True, "USER")
        patch_approval = wait(lambda: pending("patch"), "the patch approval")
        before = fingerprint(project)
        unchanged = before == {relative: hashlib.sha256(text.encode("utf-8")).hexdigest()
                               for relative, text in PROJECT.items()}
        result.update({"patch": patch_approval.detail, "origin_role": patch_approval.origin_role,
                       "project_unchanged_while_pending": unchanged})
        on_progress(f"Patch from {patch_approval.origin_role}; project unchanged while pending: {unchanged}")
        session.approve(patch_approval.id, True, "USER")
        wait(lambda: session.state.jobs.records[job_id].state == "done", "the job to finish")
        check = subprocess.run(["python", "-B", "-m", "unittest", "discover", "-s", "tests"], cwd=project,
                               capture_output=True, text=True, timeout=120)
        live_hash = hashlib.sha256((project / "inventory.py").read_bytes()).hexdigest()
        indexed = (session.knowledge.store.file_record("inventory.py") or {}).get("sha256") == live_hash
        result.update({"job": "done", "live_tests_pass": check.returncode == 0, "indexed_after_apply": indexed})
    except Exception as exc:
        result.update({"job": "error", "error": str(exc)})
    finally:
        plan.call_role, pipeline.call_role = original_plan, original_pipeline
        events = session.events_after(0)
        session.knowledge.close()
    roles_seen = [row["role"] for row in calls]
    result.update({"debugger_ran": "debugger" in roles_seen, "calls": calls,
                   "task_states": [event["data"]["state"] for event in events if event["kind"] == "task.state"],
                   "job_states": [event["data"]["state"] for event in events if event["kind"] == "job.state"],
                   "commands": [{key: event["data"].get(key) for key in ("status", "exit_code", "duration_s")}
                                for event in events if event["kind"] == "command.result"],
                   "total_seconds": round(time.monotonic() - began, 1)})
    reviewer = next((row for row in calls if row["role"] == "reviewer" and row["ok"]), None)
    result["reviewer"] = None if reviewer is None else {
        "verdict": reviewer["output"]["verdict"], "reasons": reviewer["output"]["reasons"],
        "correct": reviewer["output"]["verdict"] == "pass" and result.get("live_tests_pass", False)}
    raw = base / "result.json"
    raw.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = {key: result.get(key) for key in ("run_id", "job", "error", "debugger_ran", "origin_role",
                                                 "project_unchanged_while_pending", "live_tests_pass",
                                                 "indexed_after_apply", "reviewer", "total_seconds")}
    summary["per_role"] = [{key: row.get(key) for key in ("role", "model", "ok", "seconds")} |
                           {"loadSeconds": (row.get("trace") or {}).get("loadSeconds"),
                            "gpuFraction": row.get("gpuFraction")} for row in calls]
    probes = repo_root / "bench" / "probes"
    probes.mkdir(parents=True, exist_ok=True)
    record = {**summary, "approvals_by": result["approvals_by"], "plan": result.get("plan"),
              "patch": result.get("patch"), "task_states": result["task_states"],
              "job_states": result["job_states"], "commands": result["commands"], "raw_file": (raw.relative_to(repo_root).as_posix() if raw.is_relative_to(repo_root) else str(raw))}
    (probes / f"team-{run_id}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                                                encoding="utf-8")
    return raw, summary
