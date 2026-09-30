"""Local embedding API and hybrid-retrieval behavior using a fake server."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.knowledge.chunking import chunk_file  # noqa: E402
from local_memory_lab.knowledge.embedding import OllamaEmbedder  # noqa: E402
from local_memory_lab.knowledge.retrieval import HybridRetriever  # noqa: E402
from local_memory_lab.knowledge.store import KnowledgeStore  # noqa: E402
from local_memory_lab.workspace.paths import Workspace  # noqa: E402


class _EmbeddingHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.server.requests.append((self.path, json.loads(self.rfile.read(
            int(self.headers.get("Content-Length", "0"))))))
        if self.server.fail:
            body = b'{"error":"model unavailable"}'
            self.send_response(503)
        else:
            payload = self.server.requests[-1][1]
            inputs = payload["input"]
            vectors = [self.server.vectors.get(text, [1.0, 0.0]) for text in inputs]
            body = json.dumps({"model": payload["model"], "embeddings": vectors}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def start_fake_ollama(*, fail=False, vectors=None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EmbeddingHandler)
    server.fail = fail
    server.vectors = vectors or {}
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class RetrievalTests(unittest.TestCase):
    def test_embedder_uses_api_embed_and_batches_inputs(self):
        server, thread = start_fake_ollama(vectors={"one": [1, 0], "two": [0, 1]})
        try:
            embedder = OllamaEmbedder(base_url=f"http://127.0.0.1:{server.server_port}")
            self.assertEqual([(1.0, 0.0), (0.0, 1.0)], embedder.embed_many(["one", "two"]))
            endpoint, payload = server.requests[0]
            self.assertEqual("/api/embed", endpoint)
            self.assertEqual("nomic-embed-text", payload["model"])
            self.assertEqual(["one", "two"], payload["input"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def _store_with_vectors(self, base: Path, embedder: OllamaEmbedder) -> KnowledgeStore:
        root = base / "project"
        root.mkdir()
        content = "# Orchard\napple fruit orchard\n\n# Pie\napple fruit pie\n\n# Vehicle\nengine wheel vehicle\n"
        (root / "guide.md").write_text(content, encoding="utf-8")
        chunks, summary = chunk_file(Workspace(root), "guide.md")
        store = KnowledgeStore(root, base / "live_control")
        store.replace_file("guide.md", content, chunks, summary, graph=False)
        vectors = {"Orchard": (1.0, 0.0), "Pie": (0.8, 0.6), "Vehicle": (0.0, 1.0)}
        for row in store.list_chunks("guide.md"):
            section = row["text"].splitlines()[0].lstrip("# ")
            store.put_embedding(row["id"], embedder.model, vectors[section])
        return store

    def test_hybrid_search_combines_fts_and_cosine_scores(self):
        server, thread = start_fake_ollama(vectors={"apple fruit": [1.0, 0.0]})
        try:
            with tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                embedder = OllamaEmbedder(base_url=f"http://127.0.0.1:{server.server_port}")
                store = self._store_with_vectors(base, embedder)
                result = HybridRetriever(store, embedder).search("apple fruit", limit=3)
                self.assertEqual("hybrid", result["status"])
                self.assertIsNone(result["fallback_reason"])
                self.assertEqual(["Orchard", "Pie", "Vehicle"],
                                 [row["text"].splitlines()[0].lstrip("# ")
                                  for row in result["results"]])
                self.assertTrue(all(row["vector_score"] is not None for row in result["results"]))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_keyword_match_beats_nonmatch_when_vectors_are_equal(self):
        server, thread = start_fake_ollama(vectors={"needle": [1.0, 0.0]})
        try:
            with tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                root = base / "project"
                root.mkdir()
                content = ("# Weak\nneedle appears once\n\n"
                           "# None\nunrelated vehicle content\n")
                (root / "guide.md").write_text(content, encoding="utf-8")
                chunks, summary = chunk_file(Workspace(root), "guide.md")
                store = KnowledgeStore(root, base / "live_control")
                store.replace_file("guide.md", content, chunks, summary, graph=False)
                for row in store.list_chunks("guide.md"):
                    store.put_embedding(row["id"], "nomic-embed-text", (1.0, 0.0))

                embedder = OllamaEmbedder(base_url=f"http://127.0.0.1:{server.server_port}")
                results = HybridRetriever(store, embedder).search("needle", limit=2)["results"]
                self.assertEqual(["Weak", "None"],
                                 [row["text"].splitlines()[0].lstrip("# ") for row in results])
                self.assertEqual(results[0]["vector_rank"], results[1]["vector_rank"])
                self.assertGreater(results[0]["score"], results[1]["score"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_embedder_failure_returns_visible_keyword_only_results(self):
        server, thread = start_fake_ollama(fail=True)
        try:
            with tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                embedder = OllamaEmbedder(base_url=f"http://127.0.0.1:{server.server_port}")
                store = self._store_with_vectors(base, embedder)
                result = HybridRetriever(store, embedder).search("apple fruit", limit=5)
                self.assertEqual("keyword_only", result["status"])
                self.assertIn("HTTP 503", result["fallback_reason"])
                self.assertGreaterEqual(len(result["results"]), 2)
                self.assertTrue(all(row["vector_score"] is None for row in result["results"]))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
