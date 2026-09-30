"""Model-free source chunking and summaries."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.knowledge.chunking import chunk_file  # noqa: E402
from local_memory_lab.workspace.paths import Workspace  # noqa: E402


class ChunkingTests(unittest.TestCase):
    def test_python_definitions_imports_lines_and_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "sample.py").write_text(
                '"""Small sample."""\nimport os\nfrom pathlib import Path\n\n'
                '@staticmethod\ndef build():\n    return os.getcwd()\n\n'
                'class Widget:\n    pass\n', encoding="utf-8")
            chunks, summary = chunk_file(Workspace(root), "sample.py")
            self.assertEqual(["module", "imports", "function", "class"],
                             [chunk.kind for chunk in chunks])
            self.assertEqual([(1, 1), (2, 3), (5, 7), (9, 10)],
                             [chunk.lines for chunk in chunks])
            self.assertIn("Small sample.", summary)
            self.assertIn("function build", summary)
            self.assertIn("class Widget", summary)

    def test_markdown_headings_and_paragraph_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "guide.md").write_text(
                "Intro.\n\n# Setup\nInstall here.\n\n## Run\nRun here.\n", encoding="utf-8")
            chunks, summary = chunk_file(Workspace(root), "guide.md")
            self.assertEqual(["paragraph", "section", "section"],
                             [chunk.kind for chunk in chunks])
            self.assertEqual([(1, 2), (3, 5), (6, 7)],
                             [chunk.lines for chunk in chunks])
            self.assertEqual("Setup; Run", summary)
            (root / "plain.md").write_text("One paragraph.\n\nTwo paragraphs.", encoding="utf-8")
            plain, _ = chunk_file(Workspace(root), "plain.md")
            self.assertEqual([(1, 1), (3, 3)], [chunk.lines for chunk in plain])

    def test_workspace_exclusions_and_syntax_error_are_visible(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".lab").mkdir()
            (root / ".lab" / "secret.py").write_text("x = 1", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "excluded"):
                chunk_file(Workspace(root), ".lab/secret.py")
            (root / "broken.py").write_text("def broken(:\n", encoding="utf-8")
            chunks, summary = chunk_file(Workspace(root), "broken.py")
            self.assertEqual("source", chunks[0].kind)
            self.assertIn("syntax error at line 1", summary)


if __name__ == "__main__":
    unittest.main()
