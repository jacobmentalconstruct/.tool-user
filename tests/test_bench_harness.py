"""Fast tests for bench output validation and task-copy boundaries."""

from __future__ import annotations

import json
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.bench.harness import (  # noqa: E402
    BUILDER_MODELS, BUILDER_SCHEMA, MODEL_CONTEXT, MAX_OUTPUT_TOKENS, THINK_ENABLED, BuilderClient,
    apply_output, builder_schema, condition_input, parse_builder_output, run_attempt,
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
        punched = "def update():\n    raise NotImplementedError('fixture-001')\n"
        plain = condition_input(self.task, punched)
        packed = condition_input(self.task, plain["target_file"], {
            "budget_tokens": 6000, "used_tokens": 1, "dropped": 0, "items": []})
        self.assertEqual(plain, {key: packed[key] for key in plain})
        self.assertNotIn("context_pack", plain)
        self.assertEqual(6000, packed["context_pack"]["budget_tokens"])
        self.assertEqual("update", plain["target_function"])
        self.assertEqual("    raise NotImplementedError('fixture-001')\n",
                         plain["required_search_block"])

    def test_builder_json_shape_and_invalid_output(self):
        valid = {"edits": [{"path": "target.py", "search_block": "old",
                            "replace_block": "new"}], "new_files": [], "notes": "done"}
        self.assertEqual(valid, parse_builder_output(json.dumps(valid)))
        with self.assertRaises(ValueError):
            parse_builder_output("not json")
        with self.assertRaises(ValueError):
            parse_builder_output({"edits": [], "new_files": []})

    def test_model_request_limits_paths_context_and_generated_tokens(self):
        self.assertEqual(BUILDER_SCHEMA["properties"]["edits"]["items"]["properties"]["path"],
                         {"type": "string"})
        schema = builder_schema(self.task)
        self.assertEqual(["target.py"], schema["properties"]["edits"]["items"][
            "properties"]["path"]["enum"])
        inputs = condition_input(self.task,
                                 "raise NotImplementedError('fixture-001')\n")
        output = {"edits": [{"path": "target.py",
                             "search_block": inputs["required_search_block"],
                             "replace_block": "working"}], "new_files": [], "notes": ""}
        payload = {"message": {"content": json.dumps(output)},
                   "eval_count": 10, "eval_duration": 1_000_000_000}
        response = io.BytesIO(json.dumps(payload).encode())
        with patch("local_memory_lab.bench.harness.urlopen", return_value=response) as call:
            reply = BuilderClient(BUILDER_MODELS[0]).run(
                self.task, inputs)
        request = json.loads(call.call_args.args[0].data)
        self.assertEqual(0, request["options"]["temperature"])
        self.assertEqual(MODEL_CONTEXT, request["options"]["num_ctx"])
        self.assertEqual(MAX_OUTPUT_TOKENS, request["options"]["num_predict"])
        self.assertIs(request["think"], THINK_ENABLED)
        self.assertIn('"allowed_paths": ["target.py"]', request["messages"][1]["content"])
        self.assertIn('"required_search_block": "raise NotImplementedError',
                      request["messages"][1]["content"])
        self.assertIn("one valid JSON object", request["messages"][0]["content"])
        self.assertEqual(10, reply["eval_count"])

    def test_builder_client_allows_explicit_thinking_probe_configuration(self):
        output = {"edits": [{"path": "target.py", "search_block":
                             "raise NotImplementedError('fixture-001')\n",
                             "replace_block": "return 1\n"}],
                  "new_files": [], "notes": ""}
        response = io.BytesIO(json.dumps({"message": {"content": json.dumps(output)},
                                          "eval_count": 12,
                                          "eval_duration": 1_000_000_000}).encode())
        inputs = condition_input(self.task,
                                 "raise NotImplementedError('fixture-001')\n")
        with patch("local_memory_lab.bench.harness.urlopen", return_value=response) as call:
            reply = BuilderClient(BUILDER_MODELS[0], think=False,
                                  max_output_tokens=2048).run(self.task, inputs)
        request = json.loads(call.call_args.args[0].data)
        self.assertIs(request["think"], False)
        self.assertEqual(2048, request["options"]["num_predict"])
        self.assertEqual(json.dumps(output), reply["raw_reply"])

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
                             condition_input(self.task,
                                            "raise NotImplementedError('fixture-001')\n"))
        self.assertEqual(1, client.calls)
        self.assertTrue(result["invalid_output"])

    def test_invalid_model_json_keeps_token_usage_for_metrics(self):
        raw_reply = "not json" + ("x" * 2100)
        response = io.BytesIO(json.dumps({
            "message": {"content": raw_reply}, "eval_count": 50,
            "eval_duration": 2_000_000_000,
        }).encode())
        with patch("local_memory_lab.bench.harness.urlopen", return_value=response):
            result = run_attempt(BuilderClient(BUILDER_MODELS[0]), self.task, self.root,
                                 condition_input(self.task,
                                                "raise NotImplementedError('fixture-001')\n"))
        self.assertTrue(result["invalid_output"])
        self.assertEqual(50, result["eval_count"])
        self.assertEqual(25.0, result["tokens_per_s"])
        self.assertEqual(raw_reply[:2000], result["model_reply_excerpt"])

    def test_search_score_uses_gold_file_hit_in_top_five(self):
        from local_memory_lab.bench.harness import score_search
        self.assertEqual(1.0, score_search(["a.py", "gold.py"], ["gold.py"]))
        self.assertEqual(0.0, score_search(["a.py"], ["gold.py"]))


if __name__ == "__main__":
    unittest.main()
