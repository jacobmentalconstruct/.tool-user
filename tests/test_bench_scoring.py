"""Fast retrieval-pack checks for the T5 harness."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.bench.harness import (  # noqa: E402
    assert_no_answer_leak, build_context, pack_text, score_search,
)
from local_memory_lab.bench.tasks import punch_function  # noqa: E402


class BenchScoringTests(unittest.TestCase):
    def test_context_index_built_from_punched_copy_has_no_removed_body(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "punched"
            project.mkdir()
            source = ('def build_widget():\n'
                      '    """Build the widget."""\n'
                      '    return "SECRET_ANSWER_739"\n')
            punched, removed = punch_function(source, "build_widget", "fixture-001")
            (project / "widgets.py").write_text(punched, encoding="utf-8")
            task = {"id": "fixture-001", "goal": "Build the widget",
                    "target": {"path": "widgets.py", "function": "build_widget"}}
            pack, top = build_context(project, task, root / "control")
            assert_no_answer_leak(pack, removed)
            self.assertIn("widgets.py", top)
            self.assertNotIn("SECRET_ANSWER_739", pack_text(pack))

    def test_leak_assertion_rejects_removed_body(self):
        pack = {"items": [{"text": "return 'SECRET_ANSWER_739'"}]}
        with self.assertRaises(ValueError):
            assert_no_answer_leak(pack, "return 'SECRET_ANSWER_739'")

    def test_search_quality_is_gold_file_recall_at_five(self):
        self.assertEqual(1.0, score_search(["a.py", "b.py"], ["b.py"]))
        self.assertEqual(0.0, score_search(["a.py"], ["b.py"]))


if __name__ == "__main__":
    unittest.main()
