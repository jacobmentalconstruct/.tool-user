"""The deterministic gate: check, reviewer pass, task-file paths and patch caps (D16)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.team.gate import gate  # noqa: E402

OK = {"name": "tests", "status": "ok", "exit_code": 0}
EXISTING = {"title": "t", "description": "d", "check": "tests", "order": 1,
            "target": {"path": "pkg/a.py", "symbol": "f", "new": False}, "files": ["pkg/a.py"]}
NEW = {**EXISTING, "target": {"path": "pkg/new.py", "symbol": "", "new": True},
       "files": ["pkg/new.py"]}


def change(path: str, before: bytes | None, after: bytes | None):
    return {path: (Path("live") / path, before, after)}


class GateTests(unittest.TestCase):
    def test_passes_a_checked_reviewed_change_inside_the_task(self):
        result = gate(EXISTING, change("pkg/a.py", b"x = 1\n", b"x = 2\n"), OK, "pass")
        self.assertTrue(result.passed)
        self.assertIn("+x = 2", result.diff)
        self.assertEqual(["pkg/a.py"], result.facts["files"])

    def test_creation_is_allowed_only_at_a_listed_new_path(self):
        self.assertTrue(gate(NEW, change("pkg/new.py", None, b"y = 1\n"), OK, "pass").passed)
        unlisted = change("pkg/other.py", None, b"y = 1\n")
        self.assertEqual("path_outside_task", gate(NEW, unlisted, OK, "pass").reason)
        absent_in_existing_task = change("pkg/a.py", None, b"y = 1\n")
        self.assertEqual("path_outside_task", gate(EXISTING, absent_in_existing_task, OK, "pass").reason)

    def test_rejects_paths_outside_the_task_even_when_declared_new(self):
        changes = {**change("pkg/a.py", b"x = 1\n", b"x = 2\n"),
                   **change("pkg/declared_new.py", None, b"z = 1\n")}
        self.assertEqual("path_outside_task", gate(EXISTING, changes, OK, "pass").reason)
        self.assertEqual("path_outside_task", gate(EXISTING, change("pkg/a.py", b"x\n", None),
                                                   OK, "pass").reason)
        self.assertEqual("path_outside_task", gate(EXISTING, {}, OK, "pass").reason)

    def test_requires_the_check_at_exit_zero_and_a_reviewer_pass(self):
        edit = change("pkg/a.py", b"x = 1\n", b"x = 2\n")
        for check in ({**OK, "status": "failed", "exit_code": 1},
                      {**OK, "status": "timeout", "exit_code": -1}):
            with self.subTest(check=check):
                self.assertEqual("check_failed", gate(EXISTING, edit, check, "pass").reason)
        self.assertEqual("review_failed", gate(EXISTING, edit, OK, "fail").reason)

    def test_patch_size_cap(self):
        large = change("pkg/a.py", b"x = 1\n", ("y = '" + "z" * 41_000 + "'\n").encode())
        self.assertEqual("patch_too_large", gate(EXISTING, large, OK, "pass").reason)


if __name__ == "__main__":
    unittest.main()
