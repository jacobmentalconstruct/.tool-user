"""Goal drafts (T8, D22): code validates every draft before it may fill the New goal box (no model calls)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.team.draft import draft_text, validate_draft  # noqa: E402
from local_memory_lab.workspace.paths import Workspace  # noqa: E402
import team_fixtures  # noqa: E402,F401  (blocks every model call in the default suite)

FILES = {"team/steps.py": "def require_citation(card):\n    return card\n",
         "pkg/box.py": "class Box:\n    def put(self, item):\n        return item\n\n\ndef helper():\n    return 1\n",
         "pkg/util.py": "def tidy():\n    return 0\n",
         "other/util.py": "def tidy():\n    return 1\n",
         ".lab/allowlist.json": "{}\n"}


def draft(goal, path, shape, symbol):
    return {"goal": goal, "target": {"path": path, "shape": shape, "symbol": symbol}}


class DraftValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        for relative, text in FILES.items():
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text(text, encoding="utf-8")
        self.project = Workspace(root)

    def tearDown(self):
        self.temporary.cleanup()

    def reasons(self, *args):
        return validate_draft(draft(*args), self.project)

    def test_each_shape_is_accepted_when_its_target_and_wording_fit(self):
        for args in (("In team/steps.py, make require_citation reject short quote parts.",
                      "team/steps.py", "existing_symbol", "require_citation"),
                     ("Add a new function stale_temps to the existing file pkg/box.py.",
                      "pkg/box.py", "new_function", "stale_temps"),
                     ("Add a new method take to class Box in pkg/box.py.", "pkg/box.py", "new_method", "Box.take"),
                     ("Create the new file pkg/extra.py with one constant.", "pkg/extra.py", "new_file", "")):
            with self.subTest(shape=args[2]):
                self.assertEqual([], self.reasons(*args))

    def test_a_new_function_declared_as_a_new_file_is_rejected(self):  # the G3 confusion
        reasons = self.reasons("Add a new function stale_temps in the new file pkg/box.py.", "pkg/box.py",
                               "new_file", "")
        self.assertIn("already exists", " ".join(reasons))

    def test_the_shape_must_be_stated_in_words(self):
        reasons = self.reasons("Add stale_temps to pkg/box.py.", "pkg/box.py", "new_function", "stale_temps")
        self.assertIn("new function", " ".join(reasons))

    def test_a_symbol_that_does_not_fit_the_shape_is_rejected(self):
        for args in (("Add a new function x to the existing file pkg/box.py.", "pkg/box.py", "new_function", "Box.x"),
                     ("Add a new method take to class Box in pkg/box.py.", "pkg/box.py", "new_method", "take"),
                     ("Edit helper in pkg/box.py.", "pkg/box.py", "existing_symbol", "")):
            with self.subTest(args=args):
                self.assertTrue(self.reasons(*args))

    def test_unsafe_paths_and_the_allowlist_file_are_rejected(self):
        for path in (".lab/allowlist.json", "../outside.py", "/abs.py", "pkg\\box.py"):
            with self.subTest(path=path):
                self.assertTrue(self.reasons(f"Edit helper in {path}.", path, "existing_symbol", "helper"))

    def test_the_goal_names_its_file_unambiguously_and_no_other(self):
        self.assertEqual([], self.reasons("Make require_citation stricter in steps.py.", "team/steps.py",
                                          "existing_symbol", "require_citation"))  # a unique suffix
        self.assertIn("more than one", " ".join(self.reasons("Make tidy return 2 in util.py.", "pkg/util.py",
                                                             "existing_symbol", "tidy")))
        self.assertIn("another project file", " ".join(self.reasons(
            "Make helper in pkg/box.py match pkg/util.py.", "pkg/box.py", "existing_symbol", "helper")))
        self.assertIn("must name its file", " ".join(self.reasons("Make helper return 2.", "pkg/box.py",
                                                                  "existing_symbol", "helper")))

    def test_the_goal_is_one_bounded_line(self):
        for goal in ("Edit helper in pkg/box.py.\nThen more.", "Edit helper in pkg/box.py. " + "x" * 400, " "):
            with self.subTest(goal=goal[:20]):
                self.assertIn("one line", " ".join(self.reasons(goal, "pkg/box.py", "existing_symbol", "helper")))

    def test_the_chat_text_always_carries_the_check_first_note(self):
        for record in ({"valid": True, "goal": "g", "reasons": [], "checks": ["tests"]},
                       {"valid": False, "goal": "", "reasons": ["bad path"], "checks": []}):
            with self.subTest(valid=record["valid"]):
                self.assertIn("a check that fails before this change must exist", draft_text(record))


if __name__ == "__main__":
    unittest.main()
