"""Read-only checks for the measured builder role configuration."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RoleConfigTests(unittest.TestCase):
    def test_builder_is_measured_and_t6_assignments_are_preserved(self):
        roles = json.loads((ROOT / "roles.json").read_text(encoding="utf-8"))
        results = [json.loads(path.read_text(encoding="utf-8"))
                   for path in (ROOT / "bench" / "results").glob("*.json")]
        measured = {result["selected_builder"] for result in results}
        self.assertIn(roles["builder"]["model"], measured)
        self.assertTrue(roles["builder"]["think"])
        self.assertEqual("qwen3.5:35b", roles["planner"]["model"])
        self.assertEqual("qwen3.5:9b", roles["debugger"]["model"])
        self.assertEqual("qwen3.5:35b", roles["reviewer"]["model"])
        self.assertEqual("nomic-embed-text", roles["embedder"]["model"])


if __name__ == "__main__":
    unittest.main()
