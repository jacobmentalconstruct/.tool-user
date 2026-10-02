"""Read-only checks for the role configuration contract (D4, T6 task 1)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.team.roles import ROLES, load_roles  # noqa: E402


class RoleConfigTests(unittest.TestCase):
    def test_builder_is_measured_and_role_assignments_are_preserved(self):
        roles = json.loads((ROOT / "roles.json").read_text(encoding="utf-8"))
        results = [json.loads(path.read_text(encoding="utf-8"))
                   for path in (ROOT / "bench" / "results").glob("*.json")]
        measured = {result["selected_builder"] for result in results}
        self.assertIn(roles["builder"]["model"], measured)
        self.assertEqual("qwen2.5-coder:14b", roles["planner"]["model"])  # chosen by the T6 planner probe
        self.assertEqual("qwen3.5:9b", roles["debugger"]["model"])
        self.assertEqual("qwen2.5-coder:14b", roles["reviewer"]["model"])  # chosen by the T6 role probe
        self.assertFalse(roles["reviewer"]["think"])  # the 14b coder has no thinking mode
        self.assertEqual("nomic-embed-text", roles["embedder"]["model"])

    def test_every_role_carries_its_budget_timeout_thinking_and_keep_alive(self):
        roles = load_roles(ROOT / "roles.json")
        self.assertEqual(set(ROLES), set(roles))
        for config in roles.values():
            self.assertGreater(config.num_predict, 0)
            self.assertGreater(config.timeout_s, 0)
            self.assertTrue(config.keep_alive)
            self.assertEqual({"temperature", "top_p", "top_k", "presence_penalty", "repeat_penalty",
                              "seed", "num_ctx", "num_predict"}, set(config.options()))

    def test_builder_and_debugger_think_with_the_same_budget(self):
        # T5's probe showed think:false breaks qwen3.5:9b's schema output (D4).
        roles = load_roles(ROOT / "roles.json")
        self.assertTrue(roles["builder"].think)
        self.assertTrue(roles["debugger"].think)
        self.assertEqual(roles["builder"].num_predict, roles["debugger"].num_predict)
        self.assertEqual(roles["builder"].timeout_s, roles["debugger"].timeout_s)


if __name__ == "__main__":
    unittest.main()
