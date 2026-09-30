"""Per-project knowledge persistence, FTS5 and AST graph storage."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.knowledge.chunking import chunk_file  # noqa: E402
from local_memory_lab.knowledge.store import KnowledgeStore  # noqa: E402
from local_memory_lab.workspace.paths import Workspace  # noqa: E402


class KnowledgeStoreTests(unittest.TestCase):
    def test_project_isolation_and_database_stays_under_control_root(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            control = base / "live_control"
            left = base / "left"
            right = base / "right"
            left.mkdir()
            right.mkdir()
            (left / "a.py").write_text("LEFTTOKEN = 1\n", encoding="utf-8")
            (right / "a.py").write_text("RIGHTTOKEN = 1\n", encoding="utf-8")
            stores = [KnowledgeStore(path, control) for path in (left, right)]
            self.assertNotEqual(stores[0].path, stores[1].path)
            self.assertTrue(stores[0].path.is_relative_to(control))
            for root, store in zip((left, right), stores):
                chunks, summary = chunk_file(Workspace(root), "a.py")
                store.replace_file("a.py", (root / "a.py").read_text(), chunks, summary)
            self.assertEqual(1, len(stores[0].search_fts("LEFTTOKEN")))
            self.assertEqual([], stores[0].search_fts("RIGHTTOKEN"))
            self.assertEqual(1, len(stores[1].search_fts("RIGHTTOKEN")))
            self.assertEqual([], stores[1].search_fts("LEFTTOKEN"))

    def test_fts_embeddings_graph_and_file_data_restore_after_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            (root / "src").mkdir(parents=True)
            source_bytes = (b"from local_memory_lab.tools import assist\r\n\r\n"
                            b"def build_index():\r\n    return assist()\r\n\r\n"
                            b"class Store:\r\n    def search(self):\r\n"
                            b"        return build_index()\r\n")
            source = source_bytes.decode("utf-8").replace("\r\n", "\n")
            path = root / "src" / "store.py"
            path.write_bytes(source_bytes)
            store = KnowledgeStore(root, base / "live_control")
            chunks, summary = chunk_file(Workspace(root), "src/store.py")
            digest = store.replace_file("src/store.py", source, chunks, summary)
            self.assertEqual(hashlib.sha256(source_bytes).hexdigest(), digest)
            self.assertEqual(len(source_bytes), store.file_record("src/store.py")["size"])
            first_chunk = store.list_chunks("src/store.py")[0]
            store.put_embedding(first_chunk["id"], "nomic-embed-text", (0.25, -0.5, 1.0))
            match = store.search_fts("build_index")
            self.assertTrue(match)
            self.assertEqual("function", next(row["kind"] for row in match
                                                if row["kind"] == "function"))

            nodes, edges = store.graph()
            self.assertIn("src/store.py", [node.path for node in nodes])
            self.assertIn("Store.search", [node.name for node in nodes])
            self.assertTrue(any(edge.kind == "calls" for edge in edges))

            restored = KnowledgeStore(root, base / "live_control")
            self.assertEqual(digest, restored.file_record("src/store.py")["sha256"])
            self.assertEqual(3, len(restored.get_embeddings()[0]["vector"]))
            self.assertEqual(edges, restored.graph()[1])

    def test_replacing_file_removes_stale_fts_and_graph_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            root.mkdir()
            path = root / "sample.py"
            store = KnowledgeStore(root, base / "live_control")
            for content in ("def old_name():\n    return 'stalephrase'\n",
                            "def new_name():\n    return 'currentphrase'\n"):
                path.write_text(content, encoding="utf-8")
                chunks, summary = chunk_file(Workspace(root), "sample.py")
                store.replace_file("sample.py", content, chunks, summary)
            self.assertEqual([], store.search_fts("stalephrase"))
            self.assertTrue(store.search_fts("currentphrase"))
            nodes, _ = store.graph()
            self.assertNotIn("old_name", [node.name for node in nodes])
            self.assertIn("new_name", [node.name for node in nodes])


if __name__ == "__main__":
    unittest.main()
