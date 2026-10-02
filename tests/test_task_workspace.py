"""Disposable task workspaces leave the selected project unchanged (D16)."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.workspace.backups import BackupStore  # noqa: E402
from local_memory_lab.workspace.patching import staged_apply  # noqa: E402
from local_memory_lab.workspace.scratch import TaskWorkspace  # noqa: E402


def fingerprint(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*")) if path.is_file()}


class TaskWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.project = base / "project"
        for relative, text in {"pkg/a.py": "x = 1\n", "tests/test_a.py": "pass\n",
                               ".gitignore": "out/\n", "out/generated.txt": "build\n",
                               ".lab/allowlist.json": "{}\n", ".git/HEAD": "ref\n",
                               "pkg/__pycache__/a.pyc": "bytes\n"}.items():
            path = self.project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))
        self.scratch = base / "scratch" / "job" / "task"

    def tearDown(self):
        self.temp.cleanup()

    def test_copy_follows_the_exclusion_rules_and_records_before_bytes(self):
        workspace = TaskWorkspace.create(self.project, self.scratch, ["pkg/a.py", "pkg/new.py"])
        copied = set(fingerprint(workspace.root))
        self.assertEqual({".gitignore", "pkg/a.py", "tests/test_a.py"}, copied)
        self.assertEqual({"pkg/a.py": b"x = 1\n", "pkg/new.py": None}, workspace.before)

    def test_candidate_edits_stay_in_the_copy_and_are_reported_as_changes(self):
        original = fingerprint(self.project)
        workspace = TaskWorkspace.create(self.project, self.scratch, ["pkg/a.py", "pkg/new.py"])
        workspace.path("pkg/a.py").write_bytes(b"x = 2\n")
        workspace.path("pkg/new.py").write_bytes(b"y = 1\n")
        self.assertEqual(original, fingerprint(self.project))
        changes = workspace.changes()
        self.assertEqual((self.project / "pkg" / "a.py", b"x = 1\n", b"x = 2\n"), changes["pkg/a.py"])
        self.assertEqual((None, b"y = 1\n"), changes["pkg/new.py"][1:])
        workspace.discard()
        self.assertFalse(workspace.root.exists())
        self.assertEqual(original, fingerprint(self.project))

    def test_paths_outside_the_task_and_the_allowlist_are_refused(self):
        workspace = TaskWorkspace.create(self.project, self.scratch, ["pkg/a.py"])
        with self.assertRaises(ValueError):
            workspace.path("tests/test_a.py")
        for planned in ([".lab/allowlist.json"], ["../outside.py"], ["/abs.py"]):
            with self.subTest(planned=planned), self.assertRaises(ValueError):
                TaskWorkspace.create(self.project, self.scratch.with_name("other"), planned)

    def test_workspace_must_be_outside_the_project(self):
        with self.assertRaises(ValueError):
            TaskWorkspace.create(self.project, self.project / "scratch", ["pkg/a.py"])
        self.assertFalse((self.project / "scratch").exists())


class NewFileApplyTests(unittest.TestCase):
    """staged_apply with a None before-state: create, drift check, backup record and rollback."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "pkg").mkdir()
        self.existing = self.root / "pkg" / "a.py"
        self.existing.write_bytes(b"x = 1\n")
        self.backups = BackupStore(self.root / "backups")

    def tearDown(self):
        self.temporary.cleanup()

    def test_creates_a_new_file_and_records_it_as_absent_in_the_backup(self):
        new = self.root / "pkg" / "new.py"
        applied, generation = staged_apply({"pkg/new.py": (new, None, b"y = 1\n")}, self.backups, "r-1")
        self.assertEqual((["pkg/new.py"], b"y = 1\n"), (applied, new.read_bytes()))
        self.assertEqual([{"path": "pkg/new.py", "absent": True}], self.backups.get(generation).files)

    def test_rejects_a_new_file_whose_path_appeared_meanwhile(self):
        new = self.root / "pkg" / "new.py"
        new.write_bytes(b"someone else\n")
        with self.assertRaises(ValueError):
            staged_apply({"pkg/new.py": (new, None, b"y = 1\n")}, self.backups, "r-1")
        self.assertEqual(b"someone else\n", new.read_bytes())

    def test_rollback_removes_a_created_file_and_restores_edited_ones(self):
        new = self.root / "pkg" / "new.py"
        changes = {"pkg/new.py": (new, None, b"y = 1\n"), "pkg/a.py": (self.existing, b"x = 1\n", b"x = 2\n")}
        real_replace = os.replace
        calls = []

        def fail_second(source, target):
            calls.append(target)
            if len(calls) == 2:
                raise OSError("disk full")
            return real_replace(source, target)

        with patch("local_memory_lab.workspace.patching.os.replace", side_effect=fail_second):
            with self.assertRaises(OSError):
                staged_apply(changes, self.backups, "r-1")
        self.assertFalse(new.exists())
        self.assertEqual(b"x = 1\n", self.existing.read_bytes())


if __name__ == "__main__":
    unittest.main()
