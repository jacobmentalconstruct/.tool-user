"""Role configuration, narrow output schemas and one structured role call."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..agent.ollama import ollama_json
from ..locations import ROOT

ROLES = ("planner", "builder", "debugger", "reviewer")
REASONS = ("invalid_output", "cap_exhausted", "role_timeout", "model_unavailable", "invalid_plan",
           "check_not_exercising", "check_failed", "debug_exhausted", "review_failed", "path_outside_task",
           "patch_too_large", "stale_candidate", "patch_rejected", "approval_expired")
MAX_TASKS = 5
EXCERPT = 2000


@dataclass(frozen=True)
class RoleConfig:
    role: str
    model: str
    think: bool
    temperature: float
    num_ctx: int
    num_predict: int
    timeout_s: float
    keep_alive: str
    top_p: float
    top_k: int
    presence_penalty: float
    repeat_penalty: float
    seed: int

    def options(self) -> dict:
        """Every sampling lever, sent explicitly so no model default applies silently."""
        return {"temperature": self.temperature, "top_p": self.top_p, "top_k": self.top_k,
                "presence_penalty": self.presence_penalty, "repeat_penalty": self.repeat_penalty,
                "seed": self.seed, "num_ctx": self.num_ctx, "num_predict": self.num_predict}


def load_roles(path: Path = ROOT / "roles.json") -> dict[str, RoleConfig]:
    """Read the one role assignment file; callers hard-code none of these values."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    roles = {}
    for role in ROLES:
        item = data.get(role)
        if not isinstance(item, dict):
            raise ValueError(f"roles.json needs a {role} entry")
        checks = (("model", str, lambda v: bool(v)), ("think", bool, lambda v: True),
                  ("temperature", (int, float), lambda v: 0 <= v <= 2),
                  ("num_ctx", int, lambda v: 1024 <= v <= 131072),
                  ("num_predict", int, lambda v: 64 <= v <= 32768),
                  ("timeout_s", (int, float), lambda v: 0 < v <= 900),
                  ("keep_alive", str, lambda v: bool(v)),
                  ("top_p", (int, float), lambda v: 0 < v <= 1),
                  ("top_k", int, lambda v: 1 <= v <= 1000),
                  ("presence_penalty", (int, float), lambda v: -2 <= v <= 2),
                  ("repeat_penalty", (int, float), lambda v: 0 < v <= 2),
                  ("seed", int, lambda v: v >= 0))
        for key, kind, valid in checks:
            value = item.get(key)
            if not isinstance(value, kind) or (kind is not bool and isinstance(value, bool)) or not valid(value):
                raise ValueError(f"roles.json {role}.{key} is missing or out of range")
        roles[role] = RoleConfig(role, item["model"], item["think"], float(item["temperature"]),
                                 item["num_ctx"], item["num_predict"], float(item["timeout_s"]),
                                 item["keep_alive"], float(item["top_p"]), item["top_k"],
                                 float(item["presence_penalty"]), float(item["repeat_penalty"]), item["seed"])
    return roles


def _text(**extra) -> dict:
    return {"type": "string", **extra}


def planner_schema(checks: list[str]) -> dict:
    """Checks are limited by the schema; target paths are validated in code (team.plan)."""
    return {"type": "object", "required": ["tasks"], "additionalProperties": False, "properties": {
        "tasks": {"type": "array", "minItems": 1, "maxItems": MAX_TASKS, "items": {
            "type": "object", "required": ["title", "description", "target", "check"],
            "additionalProperties": False, "properties": {
                "title": _text(), "description": _text(),
                "target": {"type": "object", "required": ["path", "symbol", "new"],
                           "additionalProperties": False, "properties": {
                               "path": _text(), "symbol": _text(), "new": {"type": "boolean"}}},
                "check": _text(enum=list(checks))}}}}}


def candidate_schema(new_file: bool) -> dict:
    """Builder and debugger write only the replacement, or one new file's content."""
    field = "content" if new_file else "replace_block"
    return {"type": "object", "required": [field, "notes"], "additionalProperties": False,
            "properties": {field: _text(), "notes": _text()}}


REVIEWER_SCHEMA = {"type": "object", "required": ["verdict", "reasons", "quote"],
                   "additionalProperties": False,
                   "properties": {"verdict": _text(enum=["pass", "fail"]),
                                  "reasons": {"type": "array", "minItems": 1, "items": _text()},
                                  "quote": _text()}}


def check_schema(value, schema: dict, where: str = "output") -> None:
    """Validate the JSON-schema subset these role schemas use."""
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "boolean": bool}
    if kind and (not isinstance(value, types[kind]) or (kind != "boolean" and isinstance(value, bool))):
        raise ValueError(f"{where} must be a {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{where} is not an allowed value")
    if kind == "object":
        missing = [key for key in schema.get("required", []) if key not in value]
        extra = set(value) - set(schema.get("properties", {}))
        if missing or (extra and schema.get("additionalProperties") is False):
            raise ValueError(f"{where} has missing or unexpected fields")
        for key, item in value.items():
            check_schema(item, schema["properties"][key], f"{where}.{key}")
    elif kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", len(value)):
            raise ValueError(f"{where} has the wrong number of items")
        for index, item in enumerate(value):
            check_schema(item, schema["items"], f"{where}[{index}]")


class RoleOutputError(ValueError):
    """An unusable role reply, kept with what the model produced (D17 reasons)."""

    def __init__(self, message: str, *, reason: str, content: str = "", thinking: str = "",
                 eval_count: int = 0, cap_hit: bool = False, gpu_fraction: float | None = None,
                 trace: dict | None = None):
        super().__init__(message)
        self.reason = reason
        self.record = {"reason": reason, "error": message,
                       "answerExcerpt": content[:EXCERPT],
                       "thinkingExcerpt": thinking[-EXCERPT:],
                       "evalCount": eval_count, "capHit": cap_hit, "gpuFraction": gpu_fraction,
                       "trace": trace or {}}


@dataclass
class RoleReply:
    output: dict
    thinking: str
    eval_count: int
    elapsed_s: float
    tokens_per_s: float
    gpu_fraction: float | None = None
    trace: dict = field(default_factory=dict)


def make_room(model: str, transport=ollama_json) -> None:
    """Role steps run one at a time, so unload every other model before this one loads."""
    for item in transport("/api/ps", None, timeout=10).get("models", []):
        if item.get("name") != model:
            transport("/api/generate", {"model": item["name"], "keep_alive": 0}, timeout=60)


def residency(model: str, transport=ollama_json) -> tuple[float | None, str]:
    """GPU share of the loaded model (below 1 means it spilled onto the CPU) and its digest."""
    for item in transport("/api/ps", None, timeout=10).get("models", []):
        if item.get("name") == model and item.get("size"):
            return round(item.get("size_vram", 0) / item["size"], 3), str(item.get("digest", ""))[:12]
    return None, ""


def call_role(config: RoleConfig, system: str, payload: dict, schema: dict, *,
              validate: Callable[[dict], None] | None = None, transport=ollama_json) -> RoleReply:
    """Make one schema-constrained role call; unusable replies raise RoleOutputError."""
    make_room(config.model, transport)
    user = json.dumps(payload, ensure_ascii=False)
    started = time.monotonic()
    response = transport("/api/chat", {
        "model": config.model, "think": config.think, "stream": False,
        "keep_alive": config.keep_alive, "format": schema,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "options": config.options(),
    }, timeout=config.timeout_s)
    elapsed = time.monotonic() - started
    message = response.get("message") or {}
    content = message.get("content") or ""
    thinking = message.get("thinking") or ""
    eval_count = int(response.get("eval_count") or 0)
    eval_ns = int(response.get("eval_duration") or 0)
    cap_hit = eval_count >= config.num_predict
    resident, digest = residency(config.model, transport)
    prompt_tokens = int(response.get("prompt_eval_count") or 0)
    trace = {"model": config.model, "digest": digest, "think": config.think, "options": config.options(),
             "promptHash": hashlib.sha256((system + user + json.dumps(schema, sort_keys=True))
                                          .encode("utf-8")).hexdigest()[:12],
             "promptTokens": prompt_tokens,
             "nearContextLimit": prompt_tokens >= config.num_ctx - 64}  # Ollama may have cut the prompt
    try:
        output = json.loads(content)
        check_schema(output, schema)
        if validate:
            validate(output)
    except (ValueError, TypeError) as exc:
        reason = "cap_exhausted" if cap_hit else "invalid_output"
        message_text = "output cap reached before a usable reply" if cap_hit else str(exc)
        raise RoleOutputError(message_text, reason=reason, content=content, thinking=thinking,
                              eval_count=eval_count, cap_hit=cap_hit, gpu_fraction=resident,
                              trace=trace) from exc
    return RoleReply(output, thinking, eval_count, elapsed,
                     eval_count / (eval_ns / 1e9) if eval_ns > 0 else 0.0, resident, trace)
