"""Validated text replacements, unified diffs, and staged multi-file apply."""

from __future__ import annotations

import difflib
import os
import tempfile
from pathlib import Path

from .backups import BackupStore
from .paths import MAX_READ, Workspace

MAX_FILES = 8
MAX_ENTRIES = 20
MAX_DIFF = 40_000


def replace_unique(data: bytes, search: str, replacement: str) -> bytes:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("project file is not UTF-8 text") from exc
    if "\x00" in text:
        raise ValueError("project file appears to be binary")
    newline = "\r\n" if "\r\n" in text else "\n"
    before = search.replace("\r\n", "\n").replace("\n", newline)
    after = replacement.replace("\r\n", "\n").replace("\n", newline)
    count = text.count(before)
    if count != 1:
        raise ValueError(f"search block must match exactly once (found {count})")
    return text.replace(before, after, 1).encode("utf-8")


def unified_diff(relative: str, before: bytes, after: bytes) -> str:
    old = before.decode("utf-8").splitlines(keepends=True)
    new = after.decode("utf-8").splitlines(keepends=True)
    return "".join(difflib.unified_diff(old, new, fromfile="a/" + relative,
                                       tofile="b/" + relative))


def staged_apply(changes: dict[str, tuple[Path, bytes, bytes]],
                 backup: BackupStore, request_id: str) -> tuple[list[str], str]:
    if not changes:
        raise ValueError("patch contains no changes")
    originals = [(relative, before) for relative, (_path, before, _after) in changes.items()]
    for relative, (target, before, _after) in changes.items():
        if target.read_bytes() != before:
            raise ValueError(f"{relative} changed before apply")
    staged: list[tuple[Path, Path]] = []
    replaced: list[tuple[str, Path, bytes]] = []
    try:
        for relative, (target, before, after) in changes.items():
            if target.read_bytes() != before:
                raise ValueError(f"{relative} changed before apply")
            handle, temp_name = tempfile.mkstemp(prefix=".lab-stage-", dir=target.parent)
            temp = Path(temp_name)
            with os.fdopen(handle, "wb") as stream:
                stream.write(after)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.chmod(temp, target.stat().st_mode)
            except OSError:
                pass
            staged.append((temp, target))
        generation = backup.create("backup", "agent.patch", request_id, originals)
        for (relative, (target, before, _after)), (temp, _dest) in zip(changes.items(), staged):
            if target.read_bytes() != before:
                raise ValueError(f"{relative} changed before apply")
            os.replace(temp, target)
            replaced.append((relative, target, before))
        return [relative for relative in changes], generation.id
    except BaseException:
        failure = None
        for _relative, target, original in reversed(replaced):
            try:
                handle, temp_name = tempfile.mkstemp(prefix=".lab-recover-", dir=target.parent)
                with os.fdopen(handle, "wb") as stream:
                    stream.write(original)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_name, target)
            except BaseException as exc:  # retain original failure while attempting all recovery
                failure = failure or exc
        if failure:
            try:
                backup.create("recovery", "agent.patch", request_id, originals)
            except OSError:
                pass
        raise
    finally:
        for temp, _target in staged:
            temp.unlink(missing_ok=True)


def prepare(workspace: Workspace, entries: object) -> tuple[dict, str]:
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_ENTRIES:
        raise ValueError(f"provide 1 to {MAX_ENTRIES} patch entries")
    grouped: dict[str, tuple[Path, bytes, bytes]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("each patch entry must be an object")
        relative = entry.get("path")
        path = workspace.path(relative)
        if not path.is_file():
            raise ValueError(f"project file does not exist: {relative}")
        if path.stat().st_size > MAX_READ:
            raise ValueError(f"project file is too large to patch: {relative}")
        search, replacement = entry.get("search_block"), entry.get("replace_block")
        if not isinstance(search, str) or not search or not isinstance(replacement, str):
            raise ValueError("search_block must be nonempty text and replace_block must be text")
        if len(search) > MAX_READ or len(replacement) > MAX_READ:
            raise ValueError("a patch block is too large")
        key = str(path).casefold()
        if key not in grouped:
            if len(grouped) == MAX_FILES:
                raise ValueError(f"patch at most {MAX_FILES} files at a time")
            grouped[key] = (path, path.read_bytes(), path.read_bytes())
        target, before, current = grouped[key]
        after = replace_unique(current, search, replacement)
        grouped[key] = (target, before, after)
    changes = {path.relative_to(workspace.root).as_posix(): (path, before, after)
               for path, before, after in grouped.values()}
    if any(before == after for _path, before, after in changes.values()):
        raise ValueError("patch must change every targeted file")
    diff = "\n".join(unified_diff(relative, before, after).rstrip("\n")
                      for relative, (_path, before, after) in changes.items())
    if len(diff) > MAX_DIFF:
        raise ValueError("patch preview is too large; split it into smaller patches")
    return changes, diff
