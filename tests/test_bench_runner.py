"""Offline tests for benchmark aggregation, validation, recording, and resume."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.bench import runner  # noqa: E402
from local_memory_lab.bench.harness import (  # noqa: E402
    MAX_OUTPUT_TOKENS, MODEL_CONTEXT, PROMPT_VERSION, THINK_ENABLED,
)
from local_memory_lab.bench.tasks import load_tasks  # noqa: E402


class BenchRunnerTests(unittest.TestCase):
    def _result(self, task_id: str, model: str, condition: str) -> dict:
        return {"task_id": task_id, "model": model, "condition": condition,
                "status": "passed", "passed": True, "invalid_output": False,
                "elapsed_s": 2.0, "tokens_per_s": 30.0, "search_top5_recall": 1.0}

    def test_aggregate_and_selection_use_measured_condition_results(self):
        rows = [self._result("a", "qwen3.5:9b", "with_context"),
                self._result("a", "qwen3.5:9b", "without_context")]
        rows.extend([{**self._result("a", "qwen3.5:2b", condition), "passed": False,
                      "status": "failed", "invalid_output": True}
                     for condition in ("with_context", "without_context")])
        metrics = runner.aggregate_results(rows)
        self.assertEqual("qwen3.5:9b", runner.choose_builder(metrics))
        self.assertEqual(1.0, metrics["qwen3.5:9b"]["conditions"]["with_context"]["pass_rate"])

    def test_choose_builder_refuses_zero_pass_rate(self):
        rows = [{**self._result("a", "qwen3.5:9b", condition), "passed": False,
                 "status": "failed"}
                for condition in ("with_context", "without_context")]
        with self.assertRaisesRegex(ValueError, "no builder model passed"):
            runner.choose_builder(runner.aggregate_results(rows))

    def test_run_identity_uses_task_source_and_single_prompt_version(self):
        with patch.object(runner.subprocess, "run", return_value=SimpleNamespace(stdout="abc\n")):
            identity = runner._run_identity(ROOT, "test-version", [],
                                            [{"source": "self@test-source"}])
        self.assertEqual("self@test-source", identity["task_source"])
        self.assertEqual(PROMPT_VERSION, identity["prompt_protocol"])

    def test_validation_rejects_missing_attempt_and_duplicate(self):
        tasks = [{"id": "a"}]
        document = {"schema_version": runner.RESULT_SCHEMA,
                    "run_id": "20260930T000000Z-12345678", "available_models": ["qwen3.5:9b"],
                    "results": [self._result("a", "qwen3.5:9b", "with_context")]}
        self.assertTrue(runner.validate_results(document, tasks))
        document["results"].append(self._result("a", "qwen3.5:9b", "without_context"))
        self.assertEqual([], runner.validate_results(document, tasks))
        document["results"].append(document["results"][0])
        self.assertTrue(any("duplicate" in item
                            for item in runner.validate_results(document, tasks)))

    def test_recorded_validation_rejects_mismatched_metrics_and_selection(self):
        good = [self._result("a", "qwen3.5:9b", condition)
                for condition in ("with_context", "without_context")]
        poor = [{**self._result("a", "qwen3.5:2b", condition), "passed": False,
                 "status": "failed"}
                for condition in ("with_context", "without_context")]
        rows = [*good, *poor]
        document = {
            "schema_version": runner.RESULT_SCHEMA,
            "run_id": "20260930T000000Z-12345678",
            "available_models": ["qwen3.5:9b", "qwen3.5:2b"],
            "results": rows,
            "metrics": runner.aggregate_results(rows),
            "selected_builder": "qwen3.5:9b",
        }
        tasks = [{"id": "a"}]
        self.assertEqual([], runner.validate_recorded_result(document, tasks))
        document["metrics"]["qwen3.5:9b"]["pass_rate"] = 0.0
        self.assertTrue(any("stored metrics" in failure
                            for failure in runner.validate_recorded_result(document, tasks)))
        document["metrics"] = runner.aggregate_results(rows)
        document["selected_builder"] = "qwen3.5:2b"
        self.assertTrue(any("selected_builder" in failure
                            for failure in runner.validate_recorded_result(document, tasks)))

    def test_record_run_saves_metrics_and_builder_role(self):
        tasks = [{"id": "a"}]
        raw = {"schema_version": runner.RESULT_SCHEMA,
               "run_id": "20260930T000000Z-abcdef01", "available_models": ["qwen3.5:9b"],
               "results": [self._result("a", "qwen3.5:9b", condition)
                           for condition in ("with_context", "without_context")]}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_dir = root / "tasks"
            task_dir.mkdir()
            raw_path = root / "raw.json"
            raw_path.write_text(json.dumps(raw), encoding="utf-8")
            (root / "roles.json").write_text(json.dumps({"builder": {"model": "old", "think": False}}),
                                             encoding="utf-8")
            with patch.object(runner, "load_tasks", return_value=tasks):
                recorded, selected = runner.record_run(root, raw_path, task_dir)
            self.assertEqual("qwen3.5:9b", selected)
            self.assertTrue(recorded.is_file())
            roles = json.loads((root / "roles.json").read_text(encoding="utf-8"))
            self.assertEqual(selected, roles["builder"]["model"])
            self.assertEqual(runner.THINK_ENABLED, roles["builder"]["think"])

    def test_resume_skips_completed_attempt_without_network_or_models(self):
        task_dir = ROOT / "bench" / "tasks"
        tasks = load_tasks(task_dir)
        model = "qwen3.5:9b"
        first_key = (tasks[0]["id"], model, "without_context")
        partial = {
            "schema_version": runner.RESULT_SCHEMA,
            "run_id": "20260930T000000Z-12345678",
            "task_source": tasks[0]["source"],
            "candidates": list(runner.BUILDER_MODELS),
            "available_models": [model],
            "unavailable_models": [item for item in runner.BUILDER_MODELS if item != model],
            "task_timeout_s": runner.TASK_TIMEOUT,
            "model_context_tokens": MODEL_CONTEXT,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "prompt_protocol": PROMPT_VERSION,
            "think": THINK_ENABLED,
            "temperature": 0,
            "results": [self._result(*first_key)],
        }
        attempted = []

        def fake_attempt(client, task, checkout, inputs):
            condition = "with_context" if "context_pack" in inputs else "without_context"
            attempted.append((task["id"], client.model, condition))
            return self._result(task["id"], client.model, condition)

        with tempfile.TemporaryDirectory() as temp:
            raw_path = Path(temp) / "partial.json"
            raw_path.write_text(json.dumps(partial), encoding="utf-8")

            def make_copy(snapshot, task, destination):
                destination.mkdir(parents=True)
                return (f"    raise NotImplementedError('{task['id']}')\n", "removed body")

            with (patch.object(runner, "_get_json", side_effect=[{"version": "test"},
                                                                   {"models": [{"name": model}]}]),
                  patch.object(runner, "archive_snapshot", side_effect=lambda repo, commit, dest:
                               dest.mkdir(parents=True)),
                  patch.object(runner, "prepare_punched_copy", side_effect=make_copy),
                  patch.object(runner, "build_context", return_value=({}, [tasks[0]["target"]["path"]])),
                  patch.object(runner, "run_attempt", side_effect=fake_attempt)):
                output = runner.run_benchmark(ROOT, task_dir, resume_path=raw_path)

            self.assertEqual(raw_path, output)
            self.assertNotIn(first_key, attempted)
            self.assertEqual(len(tasks) * 2 - 1, len(attempted))
            complete = json.loads(raw_path.read_text(encoding="utf-8"))
            self.assertEqual([], runner.validate_results(complete, tasks))



class RawOutputLocationTests(unittest.TestCase):
    def test_raw_output_may_live_outside_git_only(self):
        repo = Path(tempfile.gettempdir()).resolve() / "a-repo"
        self.assertTrue(runner.committable(repo / "bench" / "raw.json", repo))
        self.assertFalse(runner.committable(repo / "live_control" / "tmp" / "raw.json", repo))
        self.assertFalse(runner.committable(repo.parent / "elsewhere" / "raw.json", repo))


if __name__ == "__main__":
    unittest.main()
