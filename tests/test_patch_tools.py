"""Checks for approval, backups, and all-or-nothing project edits."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE / "src"))

from local_memory_lab.agent.patch_tools import BackupStore, PatchTools  # noqa: E402
from local_memory_lab.agent.project_tools import ProjectTools  # noqa: E402


class PatchToolsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.project = self.base / "project"
        self.control = self.base / "control"
        self.project.mkdir()
        self.control.mkdir()
        self.control_patch = patch("local_memory_lab.agent.patch_tools.CONTROL", self.control)
        self.control_patch.start()

    def tearDown(self):
        self.control_patch.stop()
        self.temporary.cleanup()

    def tool(self, approval):
        return PatchTools(ProjectTools(self.project), approval, "test-request")

    def backup_store(self):
        scope = hashlib.sha256(str(self.project).casefold().encode("utf-8")).hexdigest()[:16]
        return BackupStore(self.control / "backups" / scope)

    def test_single_file_approval_preserves_newlines_and_backs_up(self):
        target = self.project / "a.txt"
        original = b"alpha\r\nbeta\r\n"
        target.write_bytes(original)
        seen = []
        result = self.tool(lambda proposal: seen.append(proposal) or True).call(
            "patch_project_file", {"path": "a.txt", "search_block": "beta", "replace_block": "gamma"})
        self.assertEqual(result["status"], "patched")
        self.assertEqual(target.read_bytes(), b"alpha\r\ngamma\r\n")
        self.assertIn("+gamma", seen[0]["diff"])
        self.assertEqual(self.backup_store().read(result["backup"], "a.txt"), original)

    def test_multi_file_patch_uses_one_backup_generation(self):
        (self.project / "a.txt").write_text("one\n", encoding="utf-8")
        (self.project / "b.txt").write_text("two\n", encoding="utf-8")
        result = self.tool(lambda _proposal: True).call("patch_project_files", {"files": [
            {"path": "a.txt", "search_block": "one", "replace_block": "first"},
            {"path": "b.txt", "search_block": "two", "replace_block": "second"},
        ]})
        self.assertEqual(result["status"], "patched")
        self.assertEqual(result["paths"], ["a.txt", "b.txt"])
        self.assertEqual((self.project / "a.txt").read_text(encoding="utf-8"), "first\n")
        self.assertEqual((self.project / "b.txt").read_text(encoding="utf-8"), "second\n")
        generation = self.backup_store().get(result["backup"])
        self.assertEqual(generation.status, "ok")
        self.assertEqual(len(generation.files), 2)

    def test_cancellation_and_source_change_leave_files_alone(self):
        target = self.project / "a.txt"
        target.write_text("old\n", encoding="utf-8")
        change = {"path": "a.txt", "search_block": "old", "replace_block": "new"}
        cancelled = self.tool(lambda _proposal: False).call("patch_project_file", change)
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(target.read_text(encoding="utf-8"), "old\n")

        def change_while_waiting(_proposal):
            target.write_text("external\n", encoding="utf-8")
            return True

        stale = self.tool(change_while_waiting).call("patch_project_file", change)
        self.assertEqual(stale["status"], "error")
        self.assertEqual(target.read_text(encoding="utf-8"), "external\n")
        self.assertEqual(self.backup_store().list(), [])


if __name__ == "__main__":
    unittest.main()
