"""Real planning before plan approval: validated, immutable tasks with visible checks (T6 task 2)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.session import SharedSession  # noqa: E402
from local_memory_lab.team.plan import PlanError, plan_detail, plan_goal, validate_plan  # noqa: E402
from local_memory_lab.team.roles import RoleOutputError, RoleReply, load_roles  # noqa: E402
from local_memory_lab.workspace.paths import Workspace  # noqa: E402
from team_fixtures import ONE_TASK, SUITE, make_project, planner, wait_for  # noqa: E402

CHECKS = {"tests": tuple(SUITE)}


def task(path="calc.py", symbol="add", new=False, check="tests", title="t", description="d"):
    return {"title": title, "description": description, "check": check,
            "target": {"path": path, "symbol": symbol, "new": new}}


class PlanValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = make_project(Path(self.temporary.name) / "project")
        (self.root / "dup.py").write_text("def f():\n    return 1\n\n\ndef f():\n    return 2\n", encoding="utf-8")
        (self.root / "shapes.py").write_text("class Box:\n    def area(self):\n        return 1\n\n\n"
                                             "class Twin:\n    pass\n\n\nclass Twin:\n    pass\n", encoding="utf-8")
        self.project = Workspace(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def test_accepts_an_existing_symbol_a_new_function_a_new_method_and_a_new_file(self):
        specs = validate_plan({"tasks": [task(), task(symbol="subtract", new=True),
                                         task(path="shapes.py", symbol="Box.volume", new=True),
                                         task(path="tests/test_more.py", symbol="", new=True)]},
                              self.project, CHECKS)
        self.assertEqual([1, 2, 3, 4], [spec["order"] for spec in specs])
        self.assertEqual(["calc.py"], specs[0]["files"])
        self.assertEqual({"path": "calc.py", "symbol": "subtract", "new": True}, specs[1]["target"])

    def test_rejects_unsafe_unknown_or_ambiguous_tasks(self):
        bad = {
            "absolute path": [task(path="C:/x/calc.py")],
            "parent path": [task(path="../calc.py")],
            "backslash path": [task(path="tests\\test_calc.py", symbol="", new=True)],
            "allowlist file": [task(path=".lab/allowlist.json", symbol="", new=True)],
            "case collision": [task(), task(path="Calc.py", symbol="sub", new=True)],
            "duplicate target": [task(), task()],
            "unknown check": [task(check="deploy")],
            "missing symbol": [task(symbol="absent")],
            "ambiguous symbol": [task(path="dup.py", symbol="f")],
            "new name already defined": [task(symbol="add", new=True)],
            "new method of a missing class": [task(symbol="Calc.add", new=True)],
            "new method of a function": [task(symbol="add.inner", new=True)],
            "new method of an ambiguous class": [task(path="shapes.py", symbol="Twin.extra", new=True)],
            "new method already defined": [task(path="shapes.py", symbol="Box.area", new=True)],
            "new name not plain": [task(path="shapes.py", symbol="Box.9x", new=True)],
            "new file exists": [task(symbol="", new=True)],
            "new file folder missing": [task(path="pkg/new.py", symbol="", new=True)],
            "missing description": [task(description=" ")],
        }
        for name, tasks in bad.items():
            with self.subTest(name), self.assertRaises(PlanError):
                validate_plan({"tasks": tasks}, self.project, CHECKS)

    def test_plan_detail_shows_each_task_and_its_exact_check_command(self):
        detail = plan_detail(validate_plan(ONE_TASK, self.project, CHECKS), CHECKS)
        self.assertIn("Task 1: Fix add", detail)
        self.assertIn("Target: calc.py :: add", detail)
        self.assertIn(json.dumps(SUITE), detail)

    def test_checks_come_from_the_live_project_allowlist(self):
        live = {"commands": {"tests": ["python", "-m", "unittest", "live-only"]}}
        (self.root / ".lab" / "allowlist.json").write_text(json.dumps(live), encoding="utf-8")
        sent = []

        def fake(config, system, payload, schema, **kwargs):
            sent.append(payload)
            return RoleReply(ONE_TASK, "", 10, 0.1, 100.0, 1.0)

        knowledge = type("NoIndex", (), {"store": None})()
        with patch("local_memory_lab.team.plan.call_role", side_effect=fake):
            specs, detail = plan_goal("fix add", self.root, knowledge, load_roles()["planner"])
        self.assertIn('"live-only"', detail)
        self.assertEqual({"tests": live["commands"]["tests"]}, sent[0]["checks"])
        self.assertNotIn(".lab/allowlist.json", sent[0]["files"])
        self.assertIn("calc.py", sent[0]["files"])
        self.assertEqual("add", specs[0]["target"]["symbol"])


class PlanningJobTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        no_models = patch("local_memory_lab.agent.ollama.urlopen",
                          side_effect=AssertionError("the default suite must never call a model"))
        no_models.start()
        self.addCleanup(no_models.stop)
        base = Path(self.temporary.name)
        self.path = base / "events.sqlite"
        self.session = SharedSession(self.path, load_models=False, start_worker=False)
        self.session.set_project_root(str(make_project(base / "project")))

    def tearDown(self):
        self.temporary.cleanup()

    def submit(self, **planner_args):
        stub = planner(**planner_args)  # stays active while the planning thread runs
        stub.start()
        self.addCleanup(stub.stop)
        return self.session.submit_goal("make add correct", "USER")

    def test_plan_approval_shows_the_planned_tasks_and_approved_tasks_cannot_change(self):
        job_id = self.submit()
        wait_for(self.session, job_id, "awaiting_plan_approval")
        approval = self.session.state.approvals.pending()[0]
        self.assertIn("Task 1: Fix add", approval.detail)
        self.assertNotEqual("make add correct", approval.detail)
        (task,) = self.session.state.tasks.for_job(job_id)
        self.assertEqual(("pending", ["calc.py"]), (task.state, task.spec["files"]))
        with patch("local_memory_lab.team.jobs.run_turn", return_value=("done", [])):
            self.session.approve(approval.id, True)
            wait_for(self.session, job_id, "done")
        with self.assertRaises(ValueError):
            self.session.state.tasks.transition_data(task.id, "building", spec={**task.spec, "files": ["x.py"]})

    def test_planner_failures_fail_the_job_visibly_without_a_plan_approval(self):
        cap = RoleOutputError("cap", reason="cap_exhausted", thinking="kept", eval_count=4096, cap_hit=True)
        bad_plan = {"tasks": [task(path="../escape.py")]}
        for name, args, reason in (("invalid output", {"error": cap}, "cap_exhausted"),
                                   ("invalid plan", {"output": bad_plan}, "invalid_plan")):
            with self.subTest(name):
                job_id = self.submit(**args)
                wait_for(self.session, job_id, "failed")
                job = self.session.state.jobs.records[job_id]
                self.assertTrue(job.reason.startswith(reason), job.reason)
                self.assertEqual([], self.session.state.tasks.for_job(job_id))
                self.assertFalse(any(item.job == job_id for item in self.session.state.approvals.records.values()))
                error = [e for e in self.session.events_after(0) if e["kind"] == "error" and e.get("job") == job_id]
                self.assertEqual(reason, error[-1]["data"]["failure"]["reason"])

    def test_rejected_plans_cancel_tasks_and_restart_fails_unfinished_tasks(self):
        job_id = self.submit()
        wait_for(self.session, job_id, "awaiting_plan_approval")
        self.session.approve(self.session.state.approvals.pending()[0].id, False)
        wait_for(self.session, job_id, "rejected")
        self.assertEqual(["cancelled"], [t.state for t in self.session.state.tasks.for_job(job_id)])

        job_id = self.submit()
        wait_for(self.session, job_id, "awaiting_plan_approval")
        restarted = SharedSession(self.path, load_models=False, start_worker=False)
        (task,) = restarted.state.tasks.for_job(job_id)
        self.assertEqual(("failed", "interrupted by restart"), (task.state, task.reason))


if __name__ == "__main__":
    unittest.main()
