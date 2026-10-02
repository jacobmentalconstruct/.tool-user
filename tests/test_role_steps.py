"""Role schemas, structured calls, thinking capture, reviewer cards and task records."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.lifecycles import Tasks  # noqa: E402
from local_memory_lab.session import SharedSession  # noqa: E402
from local_memory_lab.team.regions import find_region  # noqa: E402
from local_memory_lab.team.roles import (REVIEWER_SCHEMA, RoleConfig, RoleOutputError,  # noqa: E402
                                         call_role, candidate_schema, check_schema,
                                         planner_schema)
from local_memory_lab.team.steps import build_card, candidate_input, require_citation  # noqa: E402
import team_fixtures  # noqa: E402,F401  (blocks every model call in the default suite)

CONFIG = RoleConfig("builder", "qwen3.5:9b", True, 0.0, 16384, 512, 30.0, "1m", 0.95, 20, 1.5, 1.1, 42)
TASK = {"title": "Clamp budgets", "description": "Reject negative budgets.",
        "target": {"path": "pkg/budget.py", "symbol": "Budget.clamp", "new": False},
        "files": ["pkg/budget.py"], "check": "tests", "order": 1}
SOURCE = '''import math


class Budget:
    @staticmethod
    def clamp(value):
        return max(0, value)

    def other(self):
        return 1
'''


def reply(content: str, *, thinking: str = "", eval_count: int = 10):
    sent = []

    def transport(path, payload, timeout):
        if path == "/api/ps":
            return {"models": [{"name": "qwen3.5:9b", "size": 100, "size_vram": 80, "digest": "abcdef0123456789"},
                               {"name": "other:7b", "size": 50, "size_vram": 50}]}
        sent.append((path, payload, timeout))
        if path == "/api/generate":
            return {}
        return {"message": {"content": content, "thinking": thinking}, "prompt_eval_count": 16330,
                "eval_count": eval_count, "eval_duration": 1_000_000_000}
    return transport, sent


class RoleCallTests(unittest.TestCase):
    def test_valid_reply_uses_the_configured_budget_and_schema(self):
        schema = candidate_schema(False)
        transport, sent = reply(json.dumps({"replace_block": "x", "notes": ""}))
        result = call_role(CONFIG, "system", {"goal": "g"}, schema, transport=transport)
        self.assertEqual("x", result.output["replace_block"])
        self.assertEqual(("/api/generate", {"model": "other:7b", "keep_alive": 0}),
                         sent[0][:2])  # other models are unloaded first
        path, payload, timeout = sent[1]
        self.assertEqual(("/api/chat", 30.0), (path, timeout))
        self.assertEqual(0.8, result.gpu_fraction)  # a partly CPU-resident model is visible
        self.assertEqual((512, True, "1m", schema),
                         (payload["options"]["num_predict"], payload["think"],
                          payload["keep_alive"], payload["format"]))
        # every sampling lever is sent, so no model default applies silently
        self.assertEqual({"temperature": 0.0, "top_p": 0.95, "top_k": 20, "presence_penalty": 1.5,
                          "repeat_penalty": 1.1, "seed": 42, "num_ctx": 16384, "num_predict": 512},
                         payload["options"])
        trace = result.trace
        self.assertEqual(("abcdef012345", 16330, True, payload["options"]),
                         (trace["digest"], trace["promptTokens"], trace["nearContextLimit"], trace["options"]))
        self.assertEqual(12, len(trace["promptHash"]))

    def test_empty_reply_at_the_cap_is_cap_exhausted_with_thinking_kept(self):
        thinking = "early " + "x" * 3000 + " late reasoning"
        transport, _ = reply("", thinking=thinking, eval_count=CONFIG.num_predict)
        with self.assertRaises(RoleOutputError) as caught:
            call_role(CONFIG, "system", {}, candidate_schema(False), transport=transport)
        record = caught.exception.record
        self.assertEqual("cap_exhausted", record["reason"])
        self.assertTrue(record["capHit"])
        self.assertEqual(CONFIG.num_predict, record["evalCount"])
        self.assertEqual(2000, len(record["thinkingExcerpt"]))
        self.assertTrue(record["thinkingExcerpt"].endswith("late reasoning"))

    def test_malformed_reply_below_the_cap_is_invalid_output(self):
        for content in ("not json", json.dumps({"replace_block": "x"}),
                        json.dumps({"replace_block": "x", "notes": "", "path": "elsewhere.py"})):
            with self.subTest(content=content):
                transport, _ = reply(content)
                with self.assertRaises(RoleOutputError) as caught:
                    call_role(CONFIG, "system", {}, candidate_schema(False), transport=transport)
                self.assertEqual("invalid_output", caught.exception.reason)
                self.assertEqual(content[:2000], caught.exception.record["answerExcerpt"])

    def test_planner_schema_limits_checks_paths_and_task_count(self):
        schema = planner_schema(["tests"])
        task = {"title": "t", "description": "d", "check": "tests",
                "target": {"path": "pkg/budget.py", "symbol": "Budget.clamp", "new": False}}
        check_schema({"tasks": [task]}, schema)
        for bad in ({"tasks": []}, {"tasks": [task] * 6},
                    {"tasks": [{**task, "check": "deploy"}]},
                    {"tasks": [{**task, "target": {"path": "pkg/budget.py", "symbol": "x"}}]},
                    {"tasks": [{**task, "files": ["x.py"]}]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                check_schema(bad, schema)


class RegionAndCardTests(unittest.TestCase):
    def test_region_is_one_exact_definition_with_decorators(self):
        region = find_region(SOURCE, "Budget.clamp")
        self.assertTrue(region.startswith("    @staticmethod\n    def clamp"))
        self.assertTrue(region.endswith("return max(0, value)\n"))
        self.assertEqual(1, SOURCE.count(region))
        for missing in ("clamp", "Budget.absent"):
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                find_region(SOURCE, missing)

    def test_candidate_input_carries_the_pinned_region(self):
        inputs = candidate_input(TASK, SOURCE, find_region(SOURCE, "Budget.clamp"),
                                 feedback={"check_output": "FAILED"})
        self.assertEqual(("pkg/budget.py", "Budget.clamp"), (inputs["target_path"], inputs["symbol"]))
        self.assertIn("def clamp", inputs["region"])
        self.assertEqual("FAILED", inputs["feedback"]["check_output"])

    def test_reviewer_fail_must_cite_a_card_line(self):
        card = build_card(TASK, "    def clamp(value):\n        return max(0, value)\n",
                          "    def clamp(value):\n        return min(0, value)\n",
                          {"name": "tests", "status": "ok", "exit_code": 0},
                          {"paths_ok": True, "diff_chars": 120})
        self.assertIn("INTENT: Reject negative budgets.", card)
        validate = require_citation(card)
        validate({"verdict": "fail", "reasons": ["inverts the clamp"], "quote": "return min(0, value)"})
        validate({"verdict": "pass", "reasons": ["Does what the task says."], "quote": ""})
        validate({"verdict": "fail", "reasons": ["inverts it"],  # a multi-line quote of card lines
                  "quote": "def clamp(value):\n        return min(0, value)"})
        with self.assertRaises(ValueError):
            validate({"verdict": "fail", "reasons": ["x"], "quote": "return min(0, value)\nimport os"})
        transport, _ = reply(json.dumps({"verdict": "fail", "reasons": ["`return min(0, value)` is risky"],
                                         "quote": "looks risky"}))
        with self.assertRaises(RoleOutputError) as caught:
            call_role(CONFIG, "system", {"card": card}, REVIEWER_SCHEMA,
                      validate=validate, transport=transport)
        self.assertEqual("invalid_output", caught.exception.reason)


class TaskRecordTests(unittest.TestCase):
    def test_spec_is_fixed_by_the_first_event_and_debug_rounds_are_counted(self):
        tasks = Tasks()

        def step(target, **extra):
            data = tasks.transition_data("t-1", target, **extra)
            tasks.apply({"kind": "task.state", "job": "j-1", "task": "t-1", "data": data})

        step("pending", spec=TASK)
        for target in ("building", "testing", "debugging", "testing", "debugging", "testing"):
            step(target)
        self.assertEqual(2, tasks.records["t-1"].debug_round)
        with self.assertRaises(ValueError):
            tasks.transition_data("t-1", "debugging")
        with self.assertRaises(ValueError):
            tasks.transition_data("t-1", "reviewing", spec={**TASK, "files": ["other.py"]})
        with self.assertRaises(ValueError):
            Tasks().transition_data("t-2", "pending", spec={**TASK, "files": ["a.py", "b.py"]})

    def test_failure_record_and_approval_task_identity_survive_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.sqlite"
            session = SharedSession(path, load_models=False, start_worker=False)
            for state in ("queued", "planning", "awaiting_plan_approval", "running"):
                session.transition_job("j-1", state, goal="clamp" if state == "queued" else "")
            tasks = session.state.tasks
            for target, extra in (("pending", {"spec": TASK}), ("building", {})):
                session._record("system", "task.state",
                                tasks.transition_data("t-1", target, **extra), job="j-1", task="t-1")
            approval = session.request_approval(
                "patch", "1 file(s), +1 / -1", "diff", actor="system", job="j-1", task="t-1",
                origin_role="role:debugger", candidate="c-1")
            failure = RoleOutputError("cap", reason="cap_exhausted", thinking="kept thought",
                                      eval_count=512, cap_hit=True).record
            session._record("system", "task.state", tasks.transition_data(
                "t-1", "failed", reason="cap_exhausted", failure=failure), job="j-1", task="t-1")
            restarted = SharedSession(path, load_models=False, start_worker=False)
            task = restarted.state.tasks.records["t-1"]
            self.assertEqual(("failed", "cap_exhausted"), (task.state, task.reason))
            self.assertEqual("kept thought", task.failure["thinkingExcerpt"])
            self.assertEqual("Budget.clamp", task.spec["target"]["symbol"])
            record = restarted.state.approvals.records[approval]
            self.assertEqual(("j-1", "t-1", "role:debugger", "c-1"),
                             (record.job, record.task, record.origin_role, record.candidate))
            public = record.public(True)
            self.assertEqual(("t-1", "role:debugger"), (public["task"], public["originRole"]))

    def test_gated_patch_approvals_name_their_origin(self):
        with tempfile.TemporaryDirectory() as temp:
            session = SharedSession(Path(temp) / "events.sqlite", load_models=False,
                                    start_worker=False)
            for kwargs in ({"actor": "system"}, {"actor": "system", "origin_role": "role:reviewer",
                                                 "candidate": "c-1"},
                           {"actor": "role:planner"}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    session.request_approval("patch", "p", "d", **kwargs)


if __name__ == "__main__":
    unittest.main()
