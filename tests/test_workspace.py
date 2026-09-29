"""Path, exclusion, patch validation, and rollback checks for workspace operations."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE_ROOT / "src"))

from local_memory_lab.workspace.backups import BackupStore  # noqa: E402
from local_memory_lab.workspace.patching import prepare, staged_apply  # noqa: E402
from local_memory_lab.workspace.paths import Workspace  # noqa: E402


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "project"
        self.root.mkdir()
        self.workspace = Workspace(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def test_rejects_escape_and_excluded_paths(self):
        for relative in ("../outside", ".lab/allowlist.json", ".env", "live_control/shared.json"):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                self.workspace.path(relative)
        hidden_reference_dir = ".parts" + "-bin"
        with self.assertRaises(ValueError):
            self.workspace.path(hidden_reference_dir + "/anything")

    def test_rejects_builtin_directories_lockfiles_and_bytecode(self):
        directories = ("node_modules", ".venv", "venv", "__pycache__", "dist", "build",
                       "bin", "obj", "target", ".vscode", ".idea")
        lockfiles = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock")
        paths = [f"{name}/child.txt" for name in directories] + list(lockfiles) + ["module.pyc"]
        for relative in paths:
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                self.workspace.path(relative)

    def test_rejects_links(self):
        outside = Path(self.temporary.name) / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        try:
            (self.root / "linked.txt").symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is not available")
        with self.assertRaises(ValueError):
            self.workspace.path("linked.txt")

    def test_applies_gitignore_exclusions(self):
        (self.root / ".gitignore").write_text("private/\n*.secret\n", encoding="utf-8")
        (self.root / "private").mkdir()
        (self.root / "private" / "data.txt").write_text("x", encoding="utf-8")
        (self.root / "token.secret").write_text("x", encoding="utf-8")
        self.workspace = Workspace(self.root)
        self.assertEqual([item["name"] for item in self.workspace.list_project()["entries"]], [".gitignore"])

    def test_project_file_create_read_and_list(self):
        created = self.workspace.create_project_file("new.txt", "created text")
        self.assertEqual(created["status"], "created")
        read = self.workspace.read_project_file("new.txt")
        self.assertEqual(read["content"], "created text")
        self.assertEqual([row["name"] for row in self.workspace.list_project()["entries"]], ["new.txt"])
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.workspace.create_project_file("new.txt", "replacement")

    def test_honors_rooted_and_reincluded_gitignore_patterns(self):
        (self.root / ".gitignore").write_text(
            "/root-only.txt\n*.log\n!important.log\nfolder/\n", encoding="utf-8")
        (self.root / "root-only.txt").write_text("x", encoding="utf-8")
        (self.root / "nested").mkdir()
        (self.root / "nested" / "root-only.txt").write_text("x", encoding="utf-8")
        (self.root / "outside.log").write_text("x", encoding="utf-8")
        (self.root / "important.log").write_text("x", encoding="utf-8")
        (self.root / "folder").mkdir()
        (self.root / "folder" / "inside.txt").write_text("x", encoding="utf-8")
        self.workspace = Workspace(self.root)
        with self.assertRaises(ValueError):
            self.workspace.path("root-only.txt")
        self.assertEqual(self.workspace.path("nested/root-only.txt").name, "root-only.txt")
        with self.assertRaises(ValueError):
            self.workspace.path("outside.log")
        self.assertEqual(self.workspace.path("important.log").name, "important.log")
        with self.assertRaises(ValueError):
            self.workspace.path("folder/inside.txt")

    def test_requires_unique_search_match(self):
        (self.root / "sample.txt").write_text("same same", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "exactly once"):
            prepare(self.workspace, [{"path": "sample.txt", "search_block": "same", "replace_block": "new"}])

    def test_restores_earlier_files_when_a_later_replace_fails(self):
        a, b = self.root / "a.txt", self.root / "b.txt"
        a.write_text("old-a", encoding="utf-8")
        b.write_text("old-b", encoding="utf-8")
        changes = {"a.txt": (a, b"old-a", b"new-a"), "b.txt": (b, b"old-b", b"new-b")}
        store = BackupStore(Path(self.temporary.name) / "backups", "project", self.root)
        import local_memory_lab.workspace.patching as patching
        real_replace = patching.os.replace
        failed = False

        def fail_b_once(source, target):
            nonlocal failed
            if Path(target) == b and not failed:
                failed = True
                raise OSError("simulated second-file replace failure")
            return real_replace(source, target)

        with patch.object(patching.os, "replace", side_effect=fail_b_once):
            with self.assertRaisesRegex(OSError, "simulated"):
                staged_apply(changes, store, "rollback-test")
        self.assertEqual(a.read_text(encoding="utf-8"), "old-a")
        self.assertEqual(b.read_text(encoding="utf-8"), "old-b")


if __name__ == "__main__":
    unittest.main()
