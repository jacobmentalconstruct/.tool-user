"""G2 check (D21): .gitignore rules match project paths without regard to letter case."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class IgnoreCaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / ".gitignore").write_text("Notes.TXT\n/Out/\n*.LOG\n", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_rules_match_regardless_of_letter_case(self):
        from local_memory_lab.workspace.paths import excluded
        for relative, is_dir in (("notes.txt", False), ("out", True), ("pkg/run.log", False)):
            with self.subTest(path=relative):
                self.assertTrue(excluded(self.root, self.root / relative, is_dir))

    def test_unmatched_paths_stay_included(self):
        from local_memory_lab.workspace.paths import excluded
        for relative, is_dir in (("keep.py", False), ("pkg/out", True), ("out", False)):
            with self.subTest(path=relative):
                self.assertFalse(excluded(self.root, self.root / relative, is_dir))


if __name__ == "__main__":
    unittest.main()
