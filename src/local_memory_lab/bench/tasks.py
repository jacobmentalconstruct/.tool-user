"""Task metadata and safe function-body hole punching for the bench."""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path
from pathlib import PurePosixPath


def _functions(body: list[ast.stmt], scope: tuple[str, ...] = ()):
    for node in body:
        if isinstance(node, ast.ClassDef):
            yield from _functions(node.body, (*scope, node.name))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield ".".join((*scope, node.name)), node
            yield from _functions(node.body, (*scope, node.name))


def punch_function(source: str, qualified_name: str, marker: str = "bench task") -> tuple[str, str]:
    """Replace one Python function implementation while retaining its interface."""
    tree = ast.parse(source)
    matches = [node for name, node in _functions(tree.body) if name == qualified_name]
    if len(matches) != 1:
        raise ValueError(f"expected one function named {qualified_name!r}")
    node = matches[0]
    body = node.body
    doc = body[0] if (isinstance(body[0], ast.Expr) and
                      isinstance(body[0].value, ast.Constant) and
                      isinstance(body[0].value.value, str)) else None
    first_impl = body[1] if doc else body[0]
    if first_impl.lineno == node.lineno:
        raise ValueError("one-line function bodies cannot be hole-punched")
    lines = source.splitlines(keepends=True)
    start, end = first_impl.lineno - 1, node.end_lineno
    removed = "".join(lines[start:end])
    if not removed.strip():
        raise ValueError("function implementation body is empty")
    indent = " " * first_impl.col_offset
    newline = "\r\n" if "\r\n" in lines[start] else "\n"
    replacement = f'{indent}raise NotImplementedError({marker!r}){newline}'
    punched = "".join((*lines[:start], replacement, *lines[end:]))
    return punched, removed


def load_tasks(task_dir: Path) -> list[dict]:
    tasks = [json.loads(path.read_text(encoding="utf-8"))
             for path in sorted(task_dir.glob("*.json"))]
    if len(tasks) < 15:
        raise ValueError("the bench requires at least 15 task records")
    ids, sources = set(), set()
    test_id_pattern = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*){2,}$")
    for task in tasks:
        if not isinstance(task, dict) or not all(key in task for key in
                ("id", "source", "goal", "target", "gold_files", "check", "synthetic_reason")):
            raise ValueError("task record is missing required contract fields")
        if not isinstance(task["id"], str) or task["id"] in ids:
            raise ValueError("task IDs must be unique strings")
        ids.add(task["id"])
        if not isinstance(task["source"], str) or not re.fullmatch(r"self@[0-9a-f]{7,40}", task["source"]):
            raise ValueError(f"{task['id']}: source must pin one self commit")
        sources.add(task["source"])
        target, command = task["target"], task["check"]
        if not isinstance(task["goal"], str) or not task["goal"].strip():
            raise ValueError(f"{task['id']}: goal must be nonempty text")
        if not isinstance(target, dict) or not isinstance(target.get("path"), str) or not isinstance(
                target.get("function"), str):
            raise ValueError(f"{task['id']}: invalid target")
        target_path = PurePosixPath(target["path"])
        if target_path.is_absolute() or ".." in target_path.parts:
            raise ValueError(f"{task['id']}: target path must stay inside the repository")
        if not isinstance(task["gold_files"], list) or not task["gold_files"] or any(
                not isinstance(path, str) for path in task["gold_files"]):
            raise ValueError(f"{task['id']}: gold_files must be a nonempty list of paths")
        if target["path"] not in task["gold_files"]:
            raise ValueError(f"{task['id']}: target must be among nonempty gold_files")
        if not isinstance(command, list) or command[:4] != ["python", "-B", "-m", "unittest"] or not (
                len(command) == 5 and test_id_pattern.fullmatch(command[4])):
            raise ValueError(f"{task['id']}: check must name exactly one unittest ID")
    if len(sources) != 1 or not next(iter(sources)).startswith("self@"):
        raise ValueError("all tasks must use one pinned self@<commit> source")
    return tasks


def archive_snapshot(repo_root: Path, commit: str, destination: Path) -> None:
    result = subprocess.run(["git", "archive", "--format=tar", commit], cwd=repo_root,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=BytesIO(result.stdout), mode="r:") as archive:
        archive.extractall(destination)


def _run_check(task: dict, checkout: Path, timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(task["check"], cwd=checkout / "tests", capture_output=True,
                          text=True, timeout=timeout)


def prepare_punched_copy(snapshot: Path, task: dict, destination: Path) -> tuple[str, str]:
    """Copy the snapshot, punch one target body, and return its new text and removed body."""
    if destination.resolve().is_relative_to(snapshot.resolve()):
        raise ValueError("task copies must be created outside the pinned snapshot")
    shutil.copytree(snapshot, destination)
    root = destination.resolve()
    target = destination / task["target"]["path"]
    if not target.resolve().is_relative_to(root):
        raise ValueError(f"{task['id']}: target escapes snapshot")
    original = target.read_text(encoding="utf-8")
    punched, removed = punch_function(original, task["target"]["function"], task["id"])
    with target.open("w", encoding="utf-8", newline="") as stream:
        stream.write(punched)
    return punched, removed


def validate_baselines(repo_root: Path, task_dir: Path, timeout: float = 20) -> list[str]:
    """Run every task's one test on the pinned source and a punched disposable copy."""
    tasks = load_tasks(task_dir)
    commit = tasks[0]["source"].split("@", 1)[1]
    with tempfile.TemporaryDirectory(prefix="lab-bench-baselines-") as temporary:
        root = Path(temporary)
        snapshot = root / "snapshot"
        archive_snapshot(repo_root, commit, snapshot)
        failures = []
        for task in tasks:
            original = _run_check(task, snapshot, timeout)
            if original.returncode:
                failures.append(f"{task['id']}: pristine test failed")
                continue
            checkout = root / task["id"]
            try:
                _, removed = prepare_punched_copy(snapshot, task, checkout)
            except (OSError, UnicodeError, ValueError, SyntaxError) as exc:
                failures.append(f"{task['id']}: cannot punch target: {exc}")
                continue
            result = _run_check(task, checkout, timeout)
            if not removed.strip() or result.returncode == 0:
                failures.append(f"{task['id']}: punched test did not fail")
        return failures
