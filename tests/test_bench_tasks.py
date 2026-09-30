"""Fast tests for bench task metadata helpers and body punching."""

from __future__ import annotations

import ast
import tempfile
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.bench.tasks import (  # noqa: E402
    load_tasks, prepare_punched_copy, punch_function,
)


class BenchTaskTests(unittest.TestCase):
    def test_punch_preserves_decorators_signature_and_docstring(self):
        source = ('class Worker:\n'
                  '    @staticmethod\n'
                  '    def build(value: str) -> str:\n'
                  '        """Build a result."""\n'
                  '        return value.upper()\n')
        punched, removed = punch_function(source, "Worker.build")
        tree = ast.parse(punched)
        function = tree.body[0].body[0]
        self.assertEqual("build", function.name)
        self.assertEqual(1, len(function.decorator_list))
        self.assertEqual("value: str", ast.unparse(function.args.args[0]))
        self.assertEqual("Build a result.", ast.get_docstring(function))
        self.assertIsInstance(function.body[-1], ast.Raise)
        self.assertIn("return value.upper()", removed)

    def test_punch_handles_functions_without_docstrings(self):
        punched, removed = punch_function("def value():\n    return 4\n", "value")
        self.assertIn("raise NotImplementedError", punched)
        self.assertEqual("    return 4\n", removed)

    def test_punch_requires_one_multiline_target(self):
        with self.assertRaises(ValueError):
            punch_function("def value(): return 4\n", "value")
        with self.assertRaises(ValueError):
            punch_function("def value():\n    return 4\n", "missing")

    def test_committed_task_corpus_is_pinned_and_names_single_test_ids(self):
        tasks = load_tasks(ROOT / "bench" / "tasks")
        self.assertEqual(22, len(tasks))
        self.assertEqual({"self@c916053"}, {task["source"] for task in tasks})
        self.assertTrue(all(len(task["check"]) == 5 for task in tasks))

    def test_punched_copy_leaves_snapshot_unchanged_and_stays_outside_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot = root / "snapshot"
            snapshot.mkdir()
            source = "def answer():\n    return 42\n"
            (snapshot / "module.py").write_text(source, encoding="utf-8")
            task = {"id": "fixture-001", "target": {
                "path": "module.py", "function": "answer"}}
            copy = root / "copies" / "fixture-001"
            prepare_punched_copy(snapshot, task, copy)
            self.assertEqual(source, (snapshot / "module.py").read_text(encoding="utf-8"))
            self.assertIn("raise NotImplementedError", (copy / "module.py").read_text(encoding="utf-8"))
            with self.assertRaises(ValueError):
                prepare_punched_copy(snapshot, task, snapshot / "nested-copy")


if __name__ == "__main__":
    unittest.main()
