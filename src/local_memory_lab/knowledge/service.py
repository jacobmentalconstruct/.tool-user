"""Session-facing owner of project knowledge retrieval and context packs."""

from __future__ import annotations

from pathlib import Path

from ..locations import CONTROL
from .context_pack import assemble_context_pack
from .embedding import OllamaEmbedder
from .retrieval import HybridRetriever
from .store import KnowledgeStore


class KnowledgeService:
    def __init__(self, project_root: Path | None = None, *, control_root: Path = CONTROL,
                 embedder: OllamaEmbedder | None = None):
        self.control_root = Path(control_root)
        self.embedder = embedder or OllamaEmbedder()
        self.store: KnowledgeStore | None = None
        self.retriever: HybridRetriever | None = None
        if project_root is not None:
            self.set_project(project_root)

    def set_project(self, project_root: Path) -> None:
        self.store = KnowledgeStore(Path(project_root), self.control_root)
        self.retriever = HybridRetriever(self.store, self.embedder)

    def context_pack(self, query: str, budget_tokens: int = 6000) -> dict:
        if self.retriever is None:
            return assemble_context_pack([], budget_tokens)
        retrieval = self.retriever.search(query, limit=100)
        candidates = [{"kind": "chunk", "path": row["path"],
                       "lines": [row["line_start"], row["line_end"]],
                       "score": row["score"], "text": row["text"]}
                      for row in retrieval["results"]]
        return assemble_context_pack(candidates, budget_tokens)

    def context_for(self, query: str, budget_tokens: int = 6000) -> str:
        pack = self.context_pack(query, budget_tokens)
        return "\n\n".join(
            f"[{item['path']}:{item['lines'][0]}-{item['lines'][1]}]\n{item['text']}"
            for item in pack["items"])
