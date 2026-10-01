"""Sequential, bounded model comparisons; raw results stay outside the checkout."""

from __future__ import annotations

import json
import math
import os
import platform
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen
from uuid import uuid4

from ..knowledge.embedding import OllamaEmbedder
from .harness import (BUILDER_MODELS, CONTEXT_BUDGET, MAX_OUTPUT_TOKENS, MODEL_CONTEXT,
                      PROMPT_VERSION, THINK_ENABLED,
                      TASK_TIMEOUT, BuilderClient,
                      apply_output, assert_no_answer_leak, build_context,
                      condition_input, run_attempt, score_search)
from .tasks import archive_snapshot, load_tasks, prepare_punched_copy

OLLAMA = "http://127.0.0.1:11434"
RESULT_SCHEMA = 1


def _get_json(path: str) -> dict:
    request = Request(OLLAMA + path, headers={"Accept": "application/json"})
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def aggregate_results(results: list[dict]) -> dict[str, dict]:
    metrics = {}
    for model in BUILDER_MODELS:
        attempts = [item for item in results if item["model"] == model]
        if not attempts:
            continue
        conditions = {}
        for condition in ("without_context", "with_context"):
            rows = [item for item in attempts if item["condition"] == condition]
            conditions[condition] = {
                "attempts": len(rows),
                "pass_rate": sum(item["passed"] for item in rows) / len(rows) if rows else 0.0,
                "invalid_output_rate": (sum(item["invalid_output"] for item in rows) / len(rows)
                                         if rows else 0.0),
                "mean_elapsed_s": (sum(item["elapsed_s"] for item in rows) / len(rows)
                                   if rows else 0.0),
                "mean_tokens_per_s": (sum(item["tokens_per_s"] for item in rows) / len(rows)
                                      if rows else 0.0),
            }
        metrics[model] = {
            "attempts": len(attempts),
            "pass_rate": sum(item["passed"] for item in attempts) / len(attempts),
            "invalid_output_rate": sum(item["invalid_output"] for item in attempts) / len(attempts),
            "search_top5_recall": sum(item["search_top5_recall"] for item in attempts) / len(attempts),
            "conditions": conditions,
        }
    return metrics


def validate_results(document: dict, tasks: list[dict]) -> list[str]:
    failures = []
    if document.get("schema_version") != RESULT_SCHEMA:
        failures.append("unsupported results schema")
    if not isinstance(document.get("run_id"), str) or not re.fullmatch(
            r"\d{8}T\d{6}Z-[0-9a-f]{8}", document["run_id"]):
        failures.append("invalid run id")
    expected_ids = {task["id"] for task in tasks}
    results = document.get("results")
    available = document.get("available_models")
    if not isinstance(results, list) or not isinstance(available, list) or not available:
        return [*failures, "results and available_models are required"]
    if any(model not in BUILDER_MODELS for model in available):
        failures.append("results contain an undeclared candidate model")
    keys = set()
    for item in results:
        try:
            key = (item["task_id"], item["model"], item["condition"])
            if item["task_id"] not in expected_ids or item["model"] not in available or item[
                    "condition"] not in {"without_context", "with_context"}:
                failures.append(f"invalid result key: {key}")
            if key in keys:
                failures.append(f"duplicate result: {key}")
            keys.add(key)
            if item["status"] not in {"passed", "failed", "timeout"}:
                failures.append(f"invalid result status: {key}")
            if not isinstance(item["invalid_output"], bool):
                failures.append(f"invalid output flag missing: {key}")
            if not isinstance(item["passed"], bool):
                failures.append(f"pass flag missing: {key}")
            for field in ("elapsed_s", "tokens_per_s"):
                number = float(item[field])
                if not math.isfinite(number) or number < 0:
                    failures.append(f"invalid {field}: {key}")
            score = float(item["search_top5_recall"])
            if not math.isfinite(score) or not 0 <= score <= 1:
                failures.append(f"invalid top-five score: {key}")
        except (KeyError, TypeError, ValueError, OverflowError):
            failures.append("malformed result row")
    expected = {(task_id, model, condition) for task_id in expected_ids for model in available
                for condition in ("without_context", "with_context")}
    if keys != expected:
        failures.append(f"expected {len(expected)} unique attempts, found {len(keys)}")
    try:
        computed_metrics = aggregate_results(results)
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        failures.append(f"cannot compute metrics from result attempts: {exc}")
        return failures
    if "metrics" in document and document["metrics"] != computed_metrics:
        failures.append("stored metrics do not match the result attempts")
    if "selected_builder" in document:
        try:
            selected = choose_builder(computed_metrics)
        except (KeyError, TypeError, ValueError) as exc:
            failures.append(f"cannot derive selected builder from attempts: {exc}")
        else:
            if document["selected_builder"] != selected:
                failures.append("selected_builder does not match the measured pass rates")
    return failures


def validate_recorded_result(document: dict, tasks: list[dict]) -> list[str]:
    """Validate a committed run and its derived metrics and model choice."""
    failures = validate_results(document, tasks)
    if not isinstance(document.get("metrics"), dict):
        failures.append("committed result must include metrics")
    if not isinstance(document.get("selected_builder"), str):
        failures.append("committed result must include selected_builder")
    return failures


def _run_identity(repo_root: Path, server_version: str, missing: list[str],
                  tasks: list[dict]) -> dict:
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True,
                            text=True, check=True).stdout.strip()
    return {
        "schema_version": RESULT_SCHEMA,
        "run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8],
        "source_commit": commit,
        "task_source": tasks[0]["source"],
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "ollama_version": server_version,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "hardware": "RTX 5060 Ti 16 GB GPU, 32 GB RAM (PLAN.md T0 record)",
        "ollama_num_parallel": os.environ.get("OLLAMA_NUM_PARALLEL", "not reported by environment"),
        "temperature": 0,
        "attempts_per_condition": 1,
        "task_timeout_s": TASK_TIMEOUT,
        "model_context_tokens": MODEL_CONTEXT,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "prompt_protocol": PROMPT_VERSION,
        "think": THINK_ENABLED,
        "context_budget_tokens": CONTEXT_BUDGET,
        "sequential": True,
        "candidates": list(BUILDER_MODELS),
        "available_models": [model for model in BUILDER_MODELS if model not in missing],
        "unavailable_models": missing,
        "results": [],
    }


def _write_checkpoint(path: Path, document: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        json.dump(document, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    temporary.replace(path)


def run_benchmark(repo_root: Path, task_dir: Path, *, output_dir: Path | None = None,
                  resume_path: Path | None = None,
                  on_progress: Callable[[str], None] | None = None) -> Path:
    repo_root = repo_root.resolve()
    tasks = load_tasks(task_dir)
    try:
        server_version = str(_get_json("/api/version").get("version", "unknown"))
        tags = _get_json("/api/tags").get("models", [])
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"cannot query the local model service: {exc}") from exc
    installed = {item.get("name") for item in tags if isinstance(item, dict)}
    missing = [model for model in BUILDER_MODELS if model not in installed]
    available = [model for model in BUILDER_MODELS if model in installed]
    if not available:
        raise RuntimeError("none of the three declared builder candidates is installed")

    document = _run_identity(repo_root, server_version, missing, tasks)
    expected_keys = {(task["id"], model, condition) for task in tasks for model in available
                     for condition in ("without_context", "with_context")}
    if resume_path:
        resume_path = resume_path.resolve()
        if resume_path.is_relative_to(repo_root):
            raise ValueError("raw run checkpoints must remain outside the repository")
        document = json.loads(resume_path.read_text(encoding="utf-8"))
        if (document.get("task_source") != tasks[0]["source"] or
                document.get("available_models") != available or
                document.get("candidates") != list(BUILDER_MODELS) or
                document.get("task_timeout_s") != TASK_TIMEOUT or
                document.get("model_context_tokens") != MODEL_CONTEXT or
                document.get("max_output_tokens") != MAX_OUTPUT_TOKENS or
                document.get("prompt_protocol") != PROMPT_VERSION or
                document.get("think") is not THINK_ENABLED or
                document.get("temperature") != 0):
            raise ValueError("resume file does not match this task corpus and run configuration")
        existing = {(item.get("task_id"), item.get("model"), item.get("condition"))
                    for item in document.get("results", [])}
        if len(existing) != len(document.get("results", [])) or not existing <= expected_keys:
            raise ValueError("resume file contains an unexpected or duplicate attempt")
    else:
        output_dir = output_dir or Path(tempfile.gettempdir()) / "local-memory-lab-bench"
        output_dir = output_dir.resolve()
        if output_dir.is_relative_to(repo_root):
            raise ValueError("raw run checkpoints must remain outside the repository")
        output_dir.mkdir(parents=True, exist_ok=True)
        resume_path = output_dir / f"{document['run_id']}.json"
        _write_checkpoint(resume_path, document)
        existing = set()
    if on_progress:
        on_progress(f"Raw checkpoint: {resume_path} ({len(existing)}/{len(expected_keys)} attempts already complete)")
    embedder = OllamaEmbedder("nomic-embed-text", timeout=30)
    with tempfile.TemporaryDirectory(prefix="lab-bench-run-") as temporary:
        root = Path(temporary)
        snapshot = root / "snapshot"
        source_commit = tasks[0]["source"].split("@", 1)[1]
        archive_snapshot(repo_root, source_commit, snapshot)
        for task in tasks:
            indexed_copy = root / (task["id"] + "-indexed")
            _, removed = prepare_punched_copy(snapshot, task, indexed_copy)
            pack, top_paths = build_context(indexed_copy, task,
                                            root / "control" / task["id"], embedder=embedder)
            assert_no_answer_leak(pack, removed)
            for model in available:
                for condition in ("without_context", "with_context"):
                    key = (task["id"], model, condition)
                    if key in existing:
                        continue
                    checkout = root / f"{task['id']}-{model.replace(':', '-')}-{condition}"
                    punched_target, _ = prepare_punched_copy(snapshot, task, checkout)
                    inputs = condition_input(task, punched_target, pack if condition == "with_context" else None)
                    client = BuilderClient(model, timeout=TASK_TIMEOUT)
                    result = run_attempt(client, task, checkout, inputs)
                    result.update({"search_top5_recall": score_search(top_paths, task["gold_files"]),
                                   "search_top5_paths": top_paths})
                    document["results"].append(result)
                    existing.add(key)
                    _write_checkpoint(resume_path, document)
                    if on_progress:
                        on_progress(f"{len(existing)}/{len(expected_keys)} {task['id']} {model} "
                                     f"{condition}: {result['status']}")
                    shutil.rmtree(checkout, ignore_errors=True)
            shutil.rmtree(indexed_copy, ignore_errors=True)
    document["metrics"] = aggregate_results(document["results"])
    failures = validate_results(document, tasks)
    if failures:
        raise RuntimeError("incomplete benchmark run: " + "; ".join(failures))
    _write_checkpoint(resume_path, document)
    return resume_path


def choose_builder(metrics: dict) -> str:
    if not metrics:
        raise ValueError("no builder model has complete results")
    if max(result["pass_rate"] for result in metrics.values()) <= 0:
        raise ValueError("no builder model passed any benchmark task")
    def key(model: str):
        result = metrics[model]
        return (-result["pass_rate"], result["invalid_output_rate"],
                -result["conditions"]["with_context"]["pass_rate"],
                -result["conditions"]["without_context"]["pass_rate"],
                -result["conditions"]["with_context"]["mean_tokens_per_s"],
                BUILDER_MODELS.index(model))
    return min(metrics, key=key)


def record_run(repo_root: Path, result_path: Path, task_dir: Path) -> tuple[Path, str]:
    """Validate a raw run, then import its evidence and builder choice into the checkout."""
    tasks = load_tasks(task_dir)
    document = json.loads(result_path.read_text(encoding="utf-8"))
    failures = validate_results(document, tasks)
    if failures:
        raise ValueError("cannot record incomplete or invalid run: " + "; ".join(failures))
    document["metrics"] = aggregate_results(document["results"])
    selected = choose_builder(document["metrics"])
    document["selected_builder"] = selected
    results_dir = repo_root / "bench" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    destination = results_dir / f"{document['run_id']}.json"
    with destination.open("x", encoding="utf-8", newline="") as stream:
        json.dump(document, stream, indent=2, ensure_ascii=False)
        stream.write("\n")

    roles_path = repo_root / "roles.json"
    if roles_path.exists():
        roles = json.loads(roles_path.read_text(encoding="utf-8"))
    else:
        roles = {
            "planner": {"model": "qwen3.5:35b", "think": True, "temperature": 0.2,
                        "num_ctx": 16384},
            "builder": {"model": selected, "think": THINK_ENABLED, "temperature": 0,
                        "num_ctx": 16384},
            "debugger": {"model": "qwen3.5:9b", "think": False, "temperature": 0,
                         "num_ctx": 16384},
            "reviewer": {"model": "qwen3.5:35b", "think": True, "temperature": 0,
                         "num_ctx": 16384},
            "embedder": {"model": "nomic-embed-text"},
        }
    roles["builder"]["model"] = selected
    roles["builder"]["think"] = THINK_ENABLED
    roles_path.write_text(json.dumps(roles, indent=2) + "\n", encoding="utf-8")
    return destination, selected
