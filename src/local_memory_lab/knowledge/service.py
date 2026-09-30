"""Session-facing owner of project knowledge retrieval and context packs."""

from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path

from ..locations import CONTROL
from .context_pack import assemble_context_pack
from .chunking import chunk_file
from .embedding import EmbeddingUnavailable, OllamaEmbedder
from .retrieval import HybridRetriever
from .store import KnowledgeStore
from ..workspace.paths import Workspace


class KnowledgeService:
    def __init__(self, project_root: Path | None = None, *, control_root: Path = CONTROL,
                 embedder: OllamaEmbedder | None = None, start_worker: bool = True):
        self.control_root = Path(control_root)
        self.embedder = embedder or OllamaEmbedder()
        self._start_worker = start_worker
        self.store: KnowledgeStore | None = None
        self.retriever: HybridRetriever | None = None
        self.workspace: Workspace | None = None
        self._condition = threading.Condition()
        self._active = 0
        self._indexing = False
        self._pending_full = False
        self._pending_paths: set[str] = set()
        self._stopping = False
        self._worker = None
        self.last_error = ""
        self.last_fallback_reason: str | None = None
        if project_root is not None:
            self.set_project(project_root)

    def set_project(self, project_root: Path) -> None:
        with self._condition:
            while self._indexing:
                self._condition.wait()
            self.workspace = Workspace(Path(project_root).resolve(strict=True))
            self.store = KnowledgeStore(self.workspace.root, self.control_root)
            self.retriever = HybridRetriever(self.store, self.embedder)
            if self._start_worker and self._worker is None:
                self._worker = threading.Thread(target=self._work, daemon=True,
                                                name="knowledge-indexer")
                self._worker.start()
            self._pending_full = self._worker is not None
            self._pending_paths.clear()
            self._condition.notify_all()

    def refresh_paths(self, paths: list[str]) -> None:
        with self._condition:
            if self.store is None:
                return
            self._pending_paths.update(str(path) for path in paths)
            self._condition.notify_all()

    def begin_activity(self) -> None:
        with self._condition:
            if self._active == 0 and self.store is not None and self._worker is not None:
                self._pending_full = True
                self._condition.notify_all()
            while self._indexing:
                self._condition.wait()
            while self._active == 0 and (self._pending_full or self._pending_paths):
                self._condition.wait()
            self._active += 1

    def end_activity(self) -> None:
        with self._condition:
            self._active = max(0, self._active - 1)
            if self._active == 0 and self.store is not None and self._worker is not None:
                self._pending_full = True
            self._condition.notify_all()

    def wait_idle(self, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._indexing or self._pending_full or self._pending_paths:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def close(self, timeout: float = 2.0) -> None:
        with self._condition:
            self._stopping = True
            self._condition.notify_all()
        if self._worker is not None:
            self._worker.join(timeout)

    def _work(self) -> None:
        while True:
            with self._condition:
                while not self._stopping and (self._active or
                       (not self._pending_full and not self._pending_paths)):
                    self._condition.wait()
                if self._stopping:
                    return
                full = self._pending_full
                paths = set(self._pending_paths)
                self._pending_full = False
                self._pending_paths.clear()
                workspace, store = self.workspace, self.store
                self._indexing = True
            try:
                self.last_error = ""
                if workspace is not None and store is not None:
                    self._index_project(workspace, store, full, paths)
            except Exception as exc:
                self.last_error = str(exc)
            finally:
                with self._condition:
                    self._indexing = False
                    self._condition.notify_all()

    def _index_project(self, workspace: Workspace, store: KnowledgeStore,
                       full: bool, paths: set[str]) -> None:
        if full:
            found = set(self._source_paths(workspace))
            for relative in store.indexed_paths():
                if relative not in found:
                    store.remove_file(relative)
            paths.update(found)
        for relative in sorted(paths):
            try:
                self._index_file(workspace, store, relative)
            except Exception as exc:
                self.last_error = f"{relative}: {exc}"

    @staticmethod
    def _source_paths(workspace: Workspace):
        root = workspace.root
        if root is None:
            return
        def raise_walk_error(error):
            raise error

        for current, directories, filenames in os.walk(root, followlinks=False,
                                                      onerror=raise_walk_error):
            directory = Path(current)
            allowed = []
            for name in directories:
                child = directory / name
                try:
                    workspace.path(child.relative_to(root).as_posix())
                except ValueError:
                    continue
                if not child.is_symlink():
                    allowed.append(name)
            directories[:] = sorted(allowed)
            for name in sorted(filenames):
                child = directory / name
                if child.suffix.casefold() not in {".py", ".md", ".markdown"}:
                    continue
                relative = child.relative_to(root).as_posix()
                try:
                    workspace.path(relative)
                except ValueError:
                    continue
                yield relative

    def _index_file(self, workspace: Workspace, store: KnowledgeStore, relative: str) -> None:
        path = workspace.path(relative)
        if not path.is_file():
            store.remove_file(relative)
            return
        content = workspace.read_project_file(relative)["content"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        previous = store.file_record(relative)
        if not previous or previous["sha256"] != digest:
            chunks, summary = chunk_file(workspace, relative)
            store.replace_file(relative, content, chunks, summary)
        missing = store.chunks_without_embeddings(relative, self.embedder.model)
        if not missing:
            return
        try:
            vectors = self.embedder.embed_many([row["text"] for row in missing])
        except (EmbeddingUnavailable, OSError, ValueError):
            return
        for row, vector in zip(missing, vectors):
            store.put_embedding(row["id"], self.embedder.model, vector)

    def context_pack(self, query: str, budget_tokens: int = 6000) -> dict:
        if self.retriever is None:
            self.last_fallback_reason = None
            return assemble_context_pack([], budget_tokens)
        retrieval = self.retriever.search(query, limit=100)
        self.last_fallback_reason = retrieval["fallback_reason"]
        candidates = [{"kind": "chunk", "path": row["path"],
                       "lines": [row["line_start"], row["line_end"]],
                       "score": row["score"], "text": row["text"]}
                      for row in retrieval["results"]]
        return assemble_context_pack(candidates, budget_tokens)

    def context_for(self, query: str, budget_tokens: int = 6000) -> str:
        pack = self.context_pack(query, budget_tokens)
        items = [
            f"[{item['path']}:{item['lines'][0]}-{item['lines'][1]}]\n{item['text']}"
            for item in pack["items"]]
        if self.last_fallback_reason:
            items.insert(0, "Keyword-only knowledge fallback: " + self.last_fallback_reason)
        return "\n\n".join(items)
