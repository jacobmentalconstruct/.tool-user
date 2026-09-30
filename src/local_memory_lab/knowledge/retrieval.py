"""Hybrid FTS5 and embedding retrieval with a visible keyword fallback."""

from __future__ import annotations

import numpy as np

from .embedding import EmbeddingUnavailable, OllamaEmbedder
from .store import KnowledgeStore


class HybridRetriever:
    def __init__(self, store: KnowledgeStore, embedder: OllamaEmbedder | None = None):
        self.store = store
        self.embedder = embedder or OllamaEmbedder()

    @staticmethod
    def _ranks(scores: dict[int, float], *, descending: bool) -> dict[int, int]:
        """Rank a signal, giving equal scores the same competition rank."""
        ordered = sorted(scores.items(),
                         key=lambda item: ((-item[1] if descending else item[1]), item[0]))
        ranks = {}
        previous = None
        rank = 0
        for position, (chunk_id, score) in enumerate(ordered, 1):
            if previous is None or score != previous:
                rank = position
                previous = score
            ranks[chunk_id] = rank
        return ranks

    def search(self, query: str, limit: int = 20) -> dict:
        if not isinstance(query, str) or not query.strip() or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("Search needs nonempty text and a limit from 1 to 100.")
        keyword_rows = self.store.search_fts(query, min(100, max(20, limit * 5)))
        keyword_scores = {int(row["id"]): float(row["score"]) for row in keyword_rows}
        keyword_ranks = self._ranks(keyword_scores, descending=False)
        try:
            query_vector = np.asarray(self.embedder.embed(query), dtype=np.float32)
            norm = float(np.linalg.norm(query_vector))
            if query_vector.ndim != 1 or query_vector.size == 0 or not np.isfinite(norm) or norm == 0:
                raise EmbeddingUnavailable("Ollama returned a zero or invalid query vector.")
            query_vector /= norm
            chunks = {int(row["id"]): row for row in self.store.all_chunks()}
            vector_scores = {}
            for stored in self.store.get_embeddings(model=self.embedder.model):
                chunk_id = int(stored["chunk_id"])
                if chunk_id not in chunks:
                    continue
                vector = np.asarray(stored["vector"], dtype=np.float32)
                vector_norm = float(np.linalg.norm(vector))
                if vector.shape != query_vector.shape or not np.isfinite(vector_norm) or vector_norm == 0:
                    continue
                cosine = float(np.dot(query_vector, vector / vector_norm))
                vector_scores[chunk_id] = max(0.0, min(1.0, (cosine + 1.0) / 2.0))
            if not vector_scores:
                raise EmbeddingUnavailable("No compatible indexed embeddings are available.")
        except (EmbeddingUnavailable, OSError, ValueError) as exc:
            return self._keyword_only(keyword_rows, limit, str(exc))

        vector_ranks = self._ranks(vector_scores, descending=True)
        results = []
        for chunk_id in set(keyword_scores) | set(vector_scores):
            row = next((item for item in keyword_rows if int(item["id"]) == chunk_id), None)
            row = row or chunks[chunk_id]
            keyword_rank = keyword_ranks.get(chunk_id)
            vector_rank = vector_ranks.get(chunk_id)
            score = ((1.0 / (60 + keyword_rank) if keyword_rank is not None else 0.0) +
                     (1.0 / (60 + vector_rank) if vector_rank is not None else 0.0))
            results.append({key: row[key] for key in
                            ("id", "path", "ordinal", "line_start", "line_end", "kind", "text")})
            results[-1].update({"score": score,
                                "keyword_score": keyword_scores.get(chunk_id),
                                "vector_score": vector_scores.get(chunk_id),
                                "keyword_rank": keyword_rank,
                                "vector_rank": vector_rank})
        results.sort(key=lambda item: (-item["score"], item["path"], item["ordinal"]))
        return {"status": "hybrid", "fallback_reason": None, "results": results[:limit]}

    @staticmethod
    def _keyword_only(rows: list[dict], limit: int, reason: str) -> dict:
        results = []
        for rank, row in enumerate(rows[:limit]):
            result = {key: row[key] for key in
                      ("id", "path", "ordinal", "line_start", "line_end", "kind", "text")}
            result.update({"score": 1.0 / (rank + 1), "keyword_score": row["score"],
                           "vector_score": None})
            results.append(result)
        return {"status": "keyword_only", "fallback_reason": reason, "results": results}
