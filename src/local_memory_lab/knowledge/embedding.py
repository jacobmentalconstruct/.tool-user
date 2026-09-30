"""Small standard-library client for Ollama's local embedding API."""

from __future__ import annotations

import json
import math
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class EmbeddingUnavailable(RuntimeError):
    """The configured local embedder could not return usable vectors."""


class OllamaEmbedder:
    def __init__(self, model: str = "nomic-embed-text",
                 base_url: str = "http://127.0.0.1:11434", timeout: float = 30):
        if not model or not 0 < timeout <= 180:
            raise ValueError("A model name and timeout from 0 to 180 seconds are required.")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def embed(self, text: str) -> tuple[float, ...]:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Embedding input must be nonempty text.")
        return self.embed_many([text])[0]

    def embed_many(self, texts: list[str]) -> list[tuple[float, ...]]:
        if not isinstance(texts, list) or not texts or any(
                not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("Embedding input must be a nonempty list of text.")
        payload = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        request = Request(self.base_url + "/api/embed", data=payload,
                          headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.load(response)
        except HTTPError as exc:
            detail = exc.read(500).decode("utf-8", errors="replace")
            raise EmbeddingUnavailable(f"Ollama embedding request failed (HTTP {exc.code}): {detail}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise EmbeddingUnavailable(f"Ollama embedding request unavailable: {exc}") from exc
        except (ValueError, json.JSONDecodeError) as exc:
            raise EmbeddingUnavailable("Ollama returned invalid embedding JSON.") from exc
        rows = result.get("embeddings") if isinstance(result, dict) else None
        if not isinstance(rows, list) or len(rows) != len(texts):
            raise EmbeddingUnavailable("Ollama returned the wrong number of embeddings.")
        vectors = []
        for row in rows:
            if not isinstance(row, list) or not row:
                raise EmbeddingUnavailable("Ollama returned an empty embedding.")
            try:
                vector = tuple(float(value) for value in row)
            except (TypeError, ValueError, OverflowError) as exc:
                raise EmbeddingUnavailable("Ollama returned a nonnumeric embedding.") from exc
            if any(not math.isfinite(value) for value in vector):
                raise EmbeddingUnavailable("Ollama returned a non-finite embedding value.")
            vectors.append(vector)
        dimensions = len(vectors[0])
        if any(len(vector) != dimensions for vector in vectors):
            raise EmbeddingUnavailable("Ollama returned embeddings with inconsistent dimensions.")
        return vectors
