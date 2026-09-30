"""Fast tests for bench output validation and task-copy boundaries."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.bench.harness import (  # noqa: E402
    apply_output, condition_input, parse_builder_output, run_attempt,
)


class BenchHarnessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "task-copy"
        self.root.mkdir()
        (self.root / "target.py").write_text("VALUE = 'old'\n", encoding="utf-8")
        self.task = {"id": "fixture-001", "goal": "Update the value", "target": {
            "path": "target.py", "function": "update"}, "gold_files": ["target.py"]}

    def test_conditions_share_goal_target_and_punched_file(self):
        plain = condition_input(self.task, "def update():\n    raise NotImplementedError()\n")
        packed = condition_input(self.task, plain["target_file"], {
            "budget_tokens": 6000, "used_tokens": 1, "dropped": 0, "items": []})
        self.assertEqual(plain, {key: packed[key] for key in plain})
        self.assertNotIn("context_pack", plain)
        self.assertEqual(6000, packed["context_pack"]["budget_tokens"])

    def test_builder_json_shape_and_invalid_output(self):
        valid = {"edits": [{"path": "target.py", "search_block": "old",
                            "replace_block": "new"}], "new_files": [], "notes": "done"}
        self.assertEqual(valid, parse_builder_output(json.dumps(valid)))
        with self.assertRaises(ValueError):
            parse_builder_output("not json")
        with self.assertRaises(ValueError):
            parse_builder_output({"edits": [], "new_files": []})

    def test_apply_output_only_changes_one_declared_file(self):
        original = self.root / ".." / "original.py"
        original.write_text("VALUE = 'old'\n", encoding="utf-8")
        output = {"edits": [{"path": "target.py", "search_block": "old",
                             "replace_block": "new"}], "new_files": [], "notes": ""}
        apply_output(self.root, self.task, output)
        self.assertIn("new", (self.root / "target.py").read_text(encoding="utf-8"))
        self.assertIn("old", original.read_text(encoding="utf-8"))
        output["edits"][0]["path"] = "../outside.py"
        with self.assertRaises(ValueError):
            apply_output(self.root, self.task, output)

    def test_run_attempt_counts_invalid_output_without_retry(self):
        class InvalidClient:
            model = "qwen3.5:2b"
            calls = 0

            def run(self, task, inputs, timeout=None):
                self.calls += 1
                raise ValueError("builder output does not match the required schema")

        client = InvalidClient()
        result = run_attempt(client, self.task, self.root,
                             condition_input(self.task, "punched target"))
        self.assertEqual(1, client.calls)
        self.assertTrue(result["invalid_output"])

    def test_search_score_uses_gold_file_hit_in_top_five(self):
        from local_memory_lab.bench.harness import score_search
        self.assertEqual(1.0, score_search(["a.py", "gold.py"], ["gold.py"]))
        self.assertEqual(0.0, score_search(["a.py"], ["gold.py"]))


if __name__ == "__main__":
    unittest.main()
