"""Fast tests for bench task metadata helpers and body punching."""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.bench.tasks import punch_function  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
