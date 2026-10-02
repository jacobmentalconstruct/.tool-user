"""The planner probe's scoring, on the shared fixture project with a stubbed planner (no model)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.bench import planner_probe  # noqa: E402
from local_memory_lab.team.roles import RoleOutputError, load_roles  # noqa: E402
from team_fixtures import ONE_TASK, make_project, planner  # noqa: E402

TASK = {"id": "fix-001", "goal": "Add two numbers.", "target": {"path": "calc.py", "function": "add"}}
WRONG = {"tasks": [{**ONE_TASK["tasks"][0], "target": {"path": "calc.py", "symbol": "sub", "new": True}},
                   ONE_TASK["tasks"][0]]}


class PlannerProbeTests(unittest.TestCase):
    def test_scores_hits_first_task_hits_and_failure_reasons(self):
        with tempfile.TemporaryDirectory() as temp, patch(
                "local_memory_lab.agent.ollama.urlopen", side_effect=AssertionError("no model calls")):
            copy = make_project(Path(temp))
            knowledge = SimpleNamespace(store=None)
            config = replace(load_roles(ROOT / "roles.json")["planner"], model="m")
            cap = RoleOutputError("cap", reason="cap_exhausted", eval_count=4096, cap_hit=True)
            rows = []
            for args in ({}, {"output": WRONG}, {"output": {"tasks": [{**ONE_TASK["tasks"][0], "check": "x"}]}},
                         {"error": cap}):
                with planner(**args, target="local_memory_lab.bench.planner_probe.call_role"):
                    rows.append(planner_probe.plan_once(config, TASK, copy, knowledge))
        self.assertEqual((True, True, True), (rows[0]["valid"], rows[0]["hit"], rows[0]["first_task_hit"]))
        self.assertEqual((True, True, False, 2), (rows[1]["valid"], rows[1]["hit"], rows[1]["first_task_hit"],
                                                  rows[1]["tasks"]))
        self.assertEqual(("invalid_plan", "cap_exhausted"), (rows[2]["reason"], rows[3]["reason"]))
        summary = planner_probe.summarize(rows)["m"]
        self.assertEqual((4, 2, 2, 1), (summary["goals"], summary["valid"], summary["target_hit"],
                                        summary["first_task_hit"]))
        self.assertEqual({"invalid_plan": 1, "cap_exhausted": 1}, summary["invalid_by_reason"])


if __name__ == "__main__":
    unittest.main()
