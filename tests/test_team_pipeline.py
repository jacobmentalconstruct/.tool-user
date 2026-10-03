"""One task through the team in its workspace (T6 task 3): every path ends with a visible reason."""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.session import SharedSession  # noqa: E402
from local_memory_lab.team.pipeline import TaskCancelled, TaskFailed, run_task  # noqa: E402
from local_memory_lab.team.roles import RoleOutputError, RoleReply, load_roles  # noqa: E402
from team_fixtures import (BROKEN_ADD, FIXED_ADD, ONE_TASK, make_project, pending_patch,  # noqa: E402
                           planner, team_roles, wait_for)

SPEC = {"title": "Fix add", "description": "Return the sum of two numbers.", "check": "tests", "order": 1,
        "target": {"path": "calc.py", "symbol": "add", "new": False}, "files": ["calc.py"]}
ROLES = load_roles(ROOT / "roles.json")


def reply(output):
    return RoleReply(output, "", 5, 0.1, 50.0, 1.0)


class Team:
    """Scripted role replies: builder/debugger texts in order, then a reviewer verdict."""

    def __init__(self, texts=(FIXED_ADD,), verdict="pass", errors=()):
        self.texts, self.verdict, self.errors = list(texts), verdict, list(errors)
        self.calls, self.payloads = [], []

    def __call__(self, config, system, payload, schema, validate=None):
        self.calls.append(config.role)
        self.payloads.append(payload)
        if self.errors:
            raise self.errors.pop(0)
        if "verdict" in schema["properties"]:
            out = {"verdict": self.verdict, "reasons": ["checked"],
                   "quote": "return a + b" if self.verdict == "fail" else ""}
            if validate:
                validate(out)
            return reply(out)
        return reply({"replace_block": self.texts.pop(0), "notes": ""})


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self.temporary.name)
        self.project = make_project(base / "project")
        self.scratch = base / "scratch" / "t-1"
        self.states, self.commands = [], []

    def tearDown(self):
        self.temporary.cleanup()

    def run_team(self, team, *, knowledge=None, cancelled=lambda: False):
        task = SimpleNamespace(id="t-1", job="j-1", spec=SPEC)
        return run_task(task, self.project, self.scratch, ROLES, knowledge or SimpleNamespace(store=None),
                        step=lambda state, **extra: self.states.append(state), cancelled=cancelled,
                        on_command=self.commands.append, call=team)

    def assert_project_unchanged(self):
        self.assertEqual(BROKEN_ADD, (self.project / "calc.py").read_text(encoding="utf-8"))

    def test_a_fixed_candidate_reaches_the_gate_without_touching_the_project(self):
        team = Team()
        result = self.run_team(team)
        self.assertTrue(result.gate.passed)
        self.assertEqual(("role:builder", ["builder", "reviewer"]), (result.origin_role, team.calls))
        self.assertEqual(["building", "testing", "reviewing", "gated"], self.states)
        self.assertEqual(["failed", "ok"], [command["status"] for command in self.commands])
        self.assertIn("+    return a + b", result.gate.diff)
        self.assert_project_unchanged()
        result.workspace.discard()

    def test_a_check_that_already_passes_cannot_test_the_task(self):
        (self.project / "calc.py").write_text(FIXED_ADD, encoding="utf-8")
        team = Team()
        with self.assertRaises(TaskFailed) as caught:
            self.run_team(team)
        self.assertEqual("check_not_exercising", caught.exception.reason)
        self.assertEqual([], team.calls)
        self.assertFalse(self.scratch.exists())  # the workspace is discarded on every failure

    def test_a_target_an_earlier_task_removed_fails_as_invalid_plan(self):
        (self.project / "calc.py").write_text(BROKEN_ADD.replace("def add", "def plus"), encoding="utf-8")
        team = Team()
        with self.assertRaises(TaskFailed) as caught:
            self.run_team(team)
        self.assertEqual("invalid_plan", caught.exception.reason)
        self.assertEqual(([], [], []), (team.calls, self.states, self.commands))
        self.assertFalse(self.scratch.exists())

    def test_a_failing_check_goes_to_the_debugger_with_the_output_and_previous_attempt(self):
        team = Team(texts=[BROKEN_ADD, FIXED_ADD])
        result = self.run_team(team)
        self.assertEqual(("role:debugger", ["builder", "debugger", "reviewer"]), (result.origin_role, team.calls))
        self.assertEqual(["building", "testing", "debugging", "testing", "reviewing", "gated"], self.states)
        feedback = team.payloads[1]["feedback"]
        self.assertIn("FAILED", feedback["check_output"])
        self.assertEqual(BROKEN_ADD, feedback["previous_attempt"])
        result.workspace.discard()

    def test_debugging_stops_after_two_rounds(self):
        team = Team(texts=[BROKEN_ADD] * 3)
        with self.assertRaises(TaskFailed) as caught:
            self.run_team(team)
        self.assertEqual("debug_exhausted", caught.exception.reason)
        self.assertEqual(["builder", "debugger", "debugger"], team.calls)
        self.assert_project_unchanged()

    def test_a_reviewer_fail_and_unusable_replies_end_with_their_reason(self):
        cases = (("review_failed", Team(verdict="fail")),
                 ("invalid_output", Team(errors=[RoleOutputError("bad json", reason="invalid_output")])),
                 ("role_timeout", Team(errors=[TimeoutError()])),
                 ("model_unavailable", Team(errors=[RuntimeError("Cannot reach Ollama.")])))
        for reason, team in cases:
            with self.subTest(reason):
                with self.assertRaises(TaskFailed) as caught:
                    self.run_team(team)
                self.assertEqual(reason, caught.exception.reason)
                self.assertEqual(reason, caught.exception.failure["reason"])
                self.assert_project_unchanged()

    def test_a_capped_reply_is_retried_once_without_the_context_pack(self):
        cap = RoleOutputError("cap", reason="cap_exhausted", thinking="long", eval_count=8192, cap_hit=True)
        knowledge = SimpleNamespace(store=True, context_pack=lambda goal, budget: {"items": []})
        team = Team(errors=[cap])
        result = self.run_team(team, knowledge=knowledge)
        self.assertIn("context_pack", team.payloads[0])
        self.assertNotIn("context_pack", team.payloads[1])
        result.workspace.discard()
        team = Team(errors=[cap, cap])
        with self.assertRaises(TaskFailed) as caught:
            self.run_team(team, knowledge=knowledge)
        self.assertEqual(("cap_exhausted", "long"), (caught.exception.reason,
                                                     caught.exception.failure["thinkingExcerpt"]))

    def test_cancelling_stops_before_the_next_command(self):
        team = Team()
        with self.assertRaises(TaskCancelled):
            self.run_team(team, cancelled=lambda: len(self.commands) >= 1)
        self.assertEqual(["builder"], team.calls)
        self.assertFalse(self.scratch.exists())


class JobOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self.temporary.name)
        self.project = make_project(base / "project")
        self.session = SharedSession(base / "events.sqlite", load_models=False, start_worker=False)
        self.session.set_project_root(str(self.project))
        self.scratch = base / "control" / "scratch"
        for stub in (planner(), team_roles(), patch("local_memory_lab.team.jobs.CONTROL", base / "control")):
            stub.start()
            self.addCleanup(stub.stop)

    def tearDown(self):
        self.temporary.cleanup()

    def reach_patch_approval(self):
        job_id = self.session.submit_goal("fix add", "USER")
        wait_for(self.session, job_id, "awaiting_plan_approval")
        self.session.approve(self.session.state.approvals.pending()[0].id, True)
        return job_id, pending_patch(self.session, job_id)

    def assert_scratch_removed(self, job_id):
        deadline = time.monotonic() + 5
        while (self.scratch / job_id).exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse((self.scratch / job_id).exists())

    def test_a_done_or_cancelled_job_leaves_no_scratch_folder(self):
        job_id, approval = self.reach_patch_approval()
        self.assertTrue((self.scratch / job_id).is_dir())
        self.session.approve(approval.id, True)
        wait_for(self.session, job_id, "done", timeout=10)
        self.assert_scratch_removed(job_id)
        (self.project / "calc.py").write_text(BROKEN_ADD, encoding="utf-8")  # so the next check fails first
        job_id, _approval = self.reach_patch_approval()
        self.session.cancel_job(job_id)
        self.assert_scratch_removed(job_id)

    def test_user_rejection_of_the_patch_fails_the_job_and_changes_nothing(self):
        job_id, approval = self.reach_patch_approval()
        self.assertIn(ONE_TASK["tasks"][0]["title"], approval.summary)
        self.assertEqual(("role:builder", approval.task), (approval.origin_role, approval.task))
        self.session.approve(approval.id, False)
        wait_for(self.session, job_id, "failed", timeout=10)
        (task,) = self.session.state.tasks.for_job(job_id)
        self.assertEqual(("rejected", "patch_rejected"), (task.state, task.reason))
        self.assertEqual(BROKEN_ADD, (self.project / "calc.py").read_text(encoding="utf-8"))

    def test_an_outside_edit_while_approval_is_pending_makes_the_candidate_stale(self):
        job_id, approval = self.reach_patch_approval()
        (self.project / "calc.py").write_text("# edited by hand\n" + BROKEN_ADD, encoding="utf-8")
        self.session.approve(approval.id, True)
        wait_for(self.session, job_id, "failed", timeout=10)
        (task,) = self.session.state.tasks.for_job(job_id)
        self.assertEqual(("failed", "stale_candidate"), (task.state, task.reason))
        self.assertTrue((self.project / "calc.py").read_text(encoding="utf-8").startswith("# edited by hand"))


if __name__ == "__main__":
    unittest.main()
