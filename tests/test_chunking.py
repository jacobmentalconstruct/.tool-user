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
            self.assertEqual(["module", "imports", "function", "class_header"],
                             [chunk.kind for chunk in chunks])
            self.assertEqual([(1, 1), (2, 3), (5, 7), (9, 10)],
                             [chunk.lines for chunk in chunks])
            self.assertIn("Small sample.", summary)
            self.assertIn("function build", summary)
            self.assertIn("class Widget", summary)

    def test_classes_split_into_header_and_addressable_methods(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "session.py").write_text(
                "class SharedSession:\n"
                "    \"\"\"Owns a shared session.\"\"\"\n"
                "    def submit(self, prompt):\n        return prompt\n\n"
                "    @staticmethod\n    def status():\n        return 'ready'\n",
                encoding="utf-8")
            chunks, summary = chunk_file(Workspace(root), "session.py")
            self.assertEqual(["class_header", "method", "method"],
                             [chunk.kind for chunk in chunks])
            self.assertEqual([(1, 2), (3, 4), (6, 8)],
                             [chunk.lines for chunk in chunks])
            self.assertIn("def submit", chunks[1].text)
            self.assertIn("def status", chunks[2].text)
            self.assertIn("method SharedSession.submit", summary)
            self.assertIn("method SharedSession.status", summary)

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

    def test_markdown_headings_inside_fences_are_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "readme.md").write_text(
                "# Guide\n\n```sh\n# install first\necho ready\n```\n\n## Finish\nDone.\n",
                encoding="utf-8")
            chunks, summary = chunk_file(Workspace(root), "readme.md")
            self.assertEqual(["section", "section"], [chunk.kind for chunk in chunks])
            self.assertEqual([(1, 7), (8, 9)], [chunk.lines for chunk in chunks])
            self.assertEqual("Guide; Finish", summary)

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
