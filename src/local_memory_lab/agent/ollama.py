"""The one local Ollama HTTP transport shared by chat and role steps."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


OLLAMA_URL = "http://127.0.0.1:11434"


def ollama_json(path: str, payload: dict | None = None, timeout: float = 180) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        OLLAMA_URL + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if data is None else "POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError("Cannot reach Ollama. Start Ollama and try again.") from exc
