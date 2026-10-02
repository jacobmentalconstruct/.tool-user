"""Disposable task inputs, context packs and bounded builder attempts."""

from __future__ import annotations

import json
import re
import subprocess
import time
from copy import deepcopy
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen

from ..knowledge.embedding import OllamaEmbedder
from ..knowledge.service import KnowledgeService
from ..workspace.paths import Workspace
from .tasks import _run_check, archive_snapshot, load_tasks, prepare_punched_copy

CONTEXT_BUDGET = 6000
TASK_TIMEOUT = 180
MODEL_CONTEXT = 16384
MAX_OUTPUT_TOKENS = 4096
THINK_ENABLED = True
PROMPT_VERSION = "target-stub-one-edit-json-think-enabled-v5"
BUILDER_MODELS = ("qwen3.5:9b", "qwen3.5:4b", "qwen3.5:2b")
BUILDER_SCHEMA = {
    "type": "object", "required": ["edits", "new_files", "notes"],
    "additionalProperties": False,
    "properties": {
        "edits": {"type": "array", "minItems": 1, "maxItems": 1, "items": {"type": "object",
            "required": ["path", "search_block", "replace_block"],
            "additionalProperties": False, "properties": {
                "path": {"type": "string"}, "search_block": {"type": "string"},
                "replace_block": {"type": "string"}}}},
        "new_files": {"type": "array", "maxItems": 0, "items": {"type": "object",
            "required": ["path", "content"], "additionalProperties": False,
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}},
        "notes": {"type": "string"},
    },
}


def builder_schema(task: dict, required_search_block: str | None = None) -> dict:
    """Constrain structured output paths to this task's declared gold files."""
    schema = deepcopy(BUILDER_SCHEMA)
    paths = list(task["gold_files"])
    schema["properties"]["edits"]["items"]["properties"]["path"]["enum"] = paths
    schema["properties"]["new_files"]["items"]["properties"]["path"]["enum"] = paths
    if required_search_block is not None:
        schema["properties"]["edits"]["items"]["properties"]["search_block"] = {
            "type": "string", "const": required_search_block}
    return schema


def condition_input(task: dict, punched_target: str, context_pack: dict | None = None) -> dict:
    marker = re.escape(repr(task["id"]))
    matches = list(re.finditer(
        rf"(?m)^[ \t]*raise NotImplementedError\({marker}\)[ \t]*(?:\r?\n|$)",
        punched_target))
    if len(matches) != 1:
        raise ValueError("punched target must contain exactly one task placeholder")
    result = {"goal": task["goal"], "target_path": task["target"]["path"],
              "target_function": task["target"]["function"],
              "target_file": punched_target,
              "required_search_block": matches[0].group(0)}
    if context_pack is not None:
        result["context_pack"] = context_pack
    return result


def pack_text(pack: dict) -> str:
    return "\n".join(item["text"] for item in pack.get("items", []))


def assert_no_answer_leak(pack: dict, removed_body: str) -> None:
    if removed_body.strip() and removed_body in pack_text(pack):
        raise ValueError("context pack contains the removed function body")


class _BenchEmbedder:
    """Small deterministic embedder for tests of the indexing path."""
    model = "bench-test-embedder"

    def embed(self, text: str) -> tuple[float, ...]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: list[str]) -> list[tuple[float, ...]]:
        return [self._vector(text) for text in texts]

    @staticmethod
    def _vector(text: str) -> tuple[float, ...]:
        values = [0.0, 0.0, 0.0, 0.0]
        for index, byte in enumerate(text.casefold().encode("utf-8")):
            values[index % len(values)] += byte + 1
        return tuple(values)


def build_context(checkout: Path, task: dict, control_root: Path, *,
                  embedder: OllamaEmbedder | None = None,
                  budget: int = CONTEXT_BUDGET) -> tuple[dict, list[str]]:
    """Index the supplied task copy, then retrieve its T4 pack and top-five paths."""
    service = KnowledgeService(checkout, control_root=control_root,
                               embedder=embedder or _BenchEmbedder(), start_worker=False)
    try:
        service._index_project(service.workspace, service.store, True, set())
        retrieval = service.retriever.search(task["goal"], limit=5)
        pack = service.context_pack(task["goal"], budget)
        return pack, [row["path"] for row in retrieval["results"]]
    finally:
        service.close()


def validate_context_packs(repo_root: Path, task_dir: Path) -> list[str]:
    """Prove each context pack comes from a punched copy and omits its removed body."""
    tasks = load_tasks(task_dir)
    commit = tasks[0]["source"].split("@", 1)[1]
    failures = []
    with TemporaryDirectory(prefix="lab-bench-context-") as temporary:
        root = Path(temporary)
        snapshot = root / "snapshot"
        archive_snapshot(repo_root, commit, snapshot)
        for task in tasks:
            checkout = root / task["id"]
            try:
                _, removed = prepare_punched_copy(snapshot, task, checkout)
                pack, _ = build_context(checkout, task, root / "control" / task["id"])
                assert_no_answer_leak(pack, removed)
            except (OSError, UnicodeError, ValueError, SyntaxError) as exc:
                failures.append(f"{task['id']}: {exc}")
    return failures


def parse_builder_output(raw: str | dict) -> dict:
    try:
        result = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("builder output is not valid JSON") from exc
    if not isinstance(result, dict) or set(result) != {"edits", "new_files", "notes"}:
        raise ValueError("builder output does not match the required schema")
    if not isinstance(result["edits"], list) or not isinstance(result["new_files"], list) or not isinstance(
            result["notes"], str):
        raise ValueError("builder output fields have invalid types")
    if len(result["edits"]) != 1 or result["new_files"]:
        raise ValueError("each hole-punch task requires exactly one edit and no new files")
    for edit in result["edits"]:
        if not isinstance(edit, dict) or set(edit) != {"path", "search_block", "replace_block"} or not all(
                isinstance(edit[key], str) for key in ("path", "search_block", "replace_block")):
            raise ValueError("builder edit has an invalid shape")
    for item in result["new_files"]:
        if not isinstance(item, dict) or set(item) != {"path", "content"} or not all(
                isinstance(item[key], str) for key in ("path", "content")):
            raise ValueError("builder new file has an invalid shape")
    return result


class InvalidBuilderOutput(ValueError):
    """Invalid structured output with the model's usage counters preserved."""

    def __init__(self, message: str, *, elapsed_s: float, eval_count: int,
                 tokens_per_s: float, raw_reply: str):
        super().__init__(message)
        self.elapsed_s = elapsed_s
        self.eval_count = eval_count
        self.tokens_per_s = tokens_per_s
        self.raw_reply = raw_reply


def apply_output(checkout: Path, task: dict, output: dict) -> None:
    allowed = set(task["gold_files"])
    root = checkout.resolve()

    def target_for(relative: str) -> Path:
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts or relative not in allowed:
            raise ValueError("builder edited a path outside the task's gold files")
        target = (checkout / Path(*path.parts)).resolve()
        if not target.is_relative_to(root):
            raise ValueError("builder path escapes its disposable task copy")
        return target

    for edit in output["edits"]:
        target = target_for(edit["path"])
        content = target.read_text(encoding="utf-8")
        search = edit["search_block"]
        if not search or content.count(search) != 1:
            raise ValueError("builder search block must match exactly once")
        with target.open("w", encoding="utf-8", newline="") as stream:
            stream.write(content.replace(search, edit["replace_block"], 1))
    for item in output["new_files"]:
        target = target_for(item["path"])
        if target.exists():
            raise ValueError("builder new file already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8", newline="") as stream:
            stream.write(item["content"])


def score_search(top_paths: list[str], gold_files: list[str]) -> float:
    gold = set(gold_files)
    return sum(path in gold for path in dict.fromkeys(top_paths[:5])) / max(1, len(gold))


class BuilderClient:
    def __init__(self, model: str, *, base_url: str = "http://127.0.0.1:11434",
                 timeout: float = TASK_TIMEOUT, think: bool = THINK_ENABLED,
                 max_output_tokens: int = MAX_OUTPUT_TOKENS):
        if (model not in BUILDER_MODELS or not 0 < timeout <= TASK_TIMEOUT or
                not isinstance(think, bool) or not 0 < max_output_tokens <= 8192):
            raise ValueError("unknown builder candidate or invalid task timeout")
        self.model, self.base_url, self.timeout = model, base_url.rstrip("/"), timeout
        self.think, self.max_output_tokens = think, max_output_tokens

    def run(self, task: dict, inputs: dict, timeout: float | None = None) -> dict:
        request = Request(self.base_url + "/api/chat", data=json.dumps({
            "model": self.model,
            "think": self.think,
            "messages": [
                {"role": "system", "content": (
                    "Respond with one valid JSON object matching the supplied schema, with no "
                    "markdown or prose outside the JSON. Implement the goal by replacing the "
                    "NotImplementedError body of the one named target function. Return exactly "
                    "one edit. Set search_block exactly to required_search_block and replace it "
                    "with the function body, preserving its indentation and trailing newline. "
                    "Do not add files or unrelated edits. Use only paths explicitly listed as "
                    "allowed.")},
                {"role": "user", "content": json.dumps({
                    **inputs, "allowed_paths": task["gold_files"]}, ensure_ascii=False)},
            ],
            "format": builder_schema(task, inputs["required_search_block"]), "stream": False,
            "options": {"temperature": 0, "num_ctx": MODEL_CONTEXT,
                        "num_predict": self.max_output_tokens},
        }).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        started = time.monotonic()
        with urlopen(request, timeout=min(self.timeout, timeout or self.timeout)) as response:
            payload = json.load(response)
        elapsed = time.monotonic() - started
        message = payload.get("message", {})
        content = message.get("content", "")
        eval_count = int(payload.get("eval_count", 0) or 0)
        eval_ns = int(payload.get("eval_duration", 0) or 0)
        tokens_per_s = eval_count / (eval_ns / 1e9) if eval_ns > 0 else 0.0
        try:
            output = parse_builder_output(content)
        except ValueError as exc:
            raise InvalidBuilderOutput(str(exc), elapsed_s=elapsed, eval_count=eval_count,
                                       tokens_per_s=tokens_per_s, raw_reply=content) from exc
        return {"output": output, "raw_reply": content, "elapsed_s": elapsed,
                "eval_count": eval_count, "tokens_per_s": tokens_per_s}


def run_attempt(client: BuilderClient, task: dict, checkout: Path, inputs: dict,
                timeout: float = TASK_TIMEOUT) -> dict:
    started = time.monotonic()
    result = {"task_id": task["id"], "model": client.model,
              "condition": "with_context" if "context_pack" in inputs else "without_context",
              "invalid_output": False, "status": "failed", "error": "", "passed": False,
              "elapsed_s": 0.0, "eval_count": 0, "tokens_per_s": 0.0}
    try:
        reply = client.run(task, inputs, timeout=timeout)
        result.update({"elapsed_s": reply["elapsed_s"], "eval_count": reply["eval_count"],
                       "tokens_per_s": reply["tokens_per_s"]})
        apply_output(checkout, task, reply["output"])
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError("task attempt exceeded its time limit")
        check = _run_check(task, checkout, remaining)
        result["passed"] = check.returncode == 0
        result["status"] = "passed" if result["passed"] else "failed"
        if check.returncode:
            result["error"] = (check.stderr or check.stdout)[-4000:]
    except InvalidBuilderOutput as exc:
        result.update({"elapsed_s": exc.elapsed_s, "eval_count": exc.eval_count,
                       "tokens_per_s": exc.tokens_per_s,
                       "model_reply_excerpt": exc.raw_reply[:2000]})
        result["invalid_output"] = True
        result["error"] = str(exc)
    except (TimeoutError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
        result["invalid_output"] = isinstance(exc, ValueError)
        result["status"] = "timeout" if isinstance(exc, (TimeoutError,
                                                             subprocess.TimeoutExpired)) else "failed"
        result["error"] = str(exc)
    result["elapsed_s"] = max(result["elapsed_s"], time.monotonic() - started)
    return result
