"""G3 check (D21): leftover staged files are listed, and staged_apply refuses to write while they exist."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class NewFunctionTests(unittest.TestCase):
    def test_stale_temps_lists_leftovers_and_deletes_nothing(self):
        from local_memory_lab.workspace.patching import stale_temps
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            names = [".lab-stage-abc", ".lab-recover-def", "keep.py", "stage.txt"]
            for name in names:
                (folder / name).write_bytes(b"x\n")
            self.assertEqual({".lab-stage-abc", ".lab-recover-def"},
                             {Path(path).name for path in stale_temps(folder)})
            self.assertTrue(all((folder / name).exists() for name in names))


class ApplyTests(unittest.TestCase):
    def test_staged_apply_names_the_leftover_and_writes_nothing(self):
        from local_memory_lab.workspace.backups import BackupStore
        from local_memory_lab.workspace.patching import staged_apply
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pkg").mkdir()
            target = root / "pkg" / "a.py"
            target.write_bytes(b"x = 1\n")
            leftover = root / "pkg" / ".lab-stage-old"
            leftover.write_bytes(b"partial\n")
            with self.assertRaises(ValueError) as caught:
                staged_apply({"pkg/a.py": (target, b"x = 1\n", b"x = 2\n")}, BackupStore(root / "backups"), "r-1")
            self.assertIn(".lab-stage-old", str(caught.exception))
            self.assertEqual(b"x = 1\n", target.read_bytes())
            self.assertTrue(leftover.exists())


if __name__ == "__main__":
    unittest.main()
