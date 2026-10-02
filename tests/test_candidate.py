"""Where a reply goes: replace a pinned region, append a function, insert a method, write a file."""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.team.candidate import placement, region_of, updated_source  # noqa: E402
from local_memory_lab.team.steps import candidate_input  # noqa: E402

SOURCE = "class Box:\n    def area(self):\n        return 1\n\n\ndef helper():\n    return 2\n"


def target(symbol, new=False, path="shapes.py"):
    return {"path": path, "symbol": symbol, "new": new}


class CandidateTests(unittest.TestCase):
    def test_replaces_the_pinned_region_and_restores_a_missing_final_newline(self):
        region = region_of(SOURCE, target("helper"))
        self.assertEqual("def helper():\n    return 2\n", region)
        after = updated_source(SOURCE, target("helper"), "def helper():\n    return 3")
        self.assertTrue(after.endswith("def helper():\n    return 3\n"))
        ast.parse(after)

    def test_appends_a_new_function_and_inserts_a_new_method_at_the_end_of_its_class(self):
        self.assertEqual("", region_of(SOURCE, target("extra", new=True)))
        after = updated_source(SOURCE, target("extra", new=True), "def extra():\n    return 4\n")
        self.assertTrue(after.endswith("\n\n\ndef extra():\n    return 4\n"))
        method = updated_source(SOURCE, target("Box.volume", new=True), "    def volume(self):\n        return 8\n")
        tree = ast.parse(method)
        self.assertEqual(["area", "volume"], [node.name for node in tree.body[0].body])
        self.assertIn("def helper", method)

    def test_writes_a_new_file_and_keeps_windows_line_endings(self):
        self.assertEqual("x = 1\n", updated_source("", target("", new=True), "x = 1"))
        crlf = SOURCE.replace("\n", "\r\n")
        after = updated_source(crlf, target("helper"), "def helper():\n    return 3\n")
        self.assertNotIn("\n", after.replace("\r\n", ""))

    def test_builder_input_names_the_placement_and_only_new_files_are_new_files(self):
        spec = {"title": "t", "description": "d", "target": target("Box.volume", new=True)}
        inputs = candidate_input(spec, SOURCE, "")
        self.assertFalse(inputs["new_file"])
        self.assertIn("end of class Box", inputs["placement"])
        self.assertIn("whole content", placement(target("", new=True)))


if __name__ == "__main__":
    unittest.main()
