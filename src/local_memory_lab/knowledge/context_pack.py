"""Deterministic greedy context-pack assembly."""

from __future__ import annotations

import math
from collections.abc import Iterable


def assemble_context_pack(candidates: Iterable[dict], budget_tokens: int) -> dict:
    if not isinstance(budget_tokens, int) or isinstance(budget_tokens, bool) or budget_tokens < 1:
        raise ValueError("Context budget must be a positive integer.")
    ordered = sorted(candidates, key=lambda item: (
        -float(item.get("score", 0.0)), str(item.get("path", "")),
        int((item.get("lines") or [0])[0]), str(item.get("kind", "")),
        str(item.get("text", ""))))
    items = []
    used = dropped = 0
    for candidate in ordered:
        text = str(candidate.get("text", ""))
        cost = math.ceil(len(text) / 4)
        if used + cost <= budget_tokens:
            items.append({"kind": str(candidate.get("kind", "chunk")),
                          "path": str(candidate.get("path", "")),
                          "lines": list(candidate.get("lines", [])),
                          "score": float(candidate.get("score", 0.0)),
                          "text": text})
            used += cost
        else:
            dropped += 1
    return {"budget_tokens": budget_tokens, "used_tokens": used,
            "dropped": dropped, "items": items}
