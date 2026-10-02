"""Unified diffs and the staged, all-or-nothing multi-file apply."""

from __future__ import annotations

import difflib
import os
import tempfile
from pathlib import Path

from .backups import BackupStore

MAX_FILES = 8
MAX_DIFF = 40_000


def unified_diff(relative: str, before: bytes, after: bytes) -> str:
    old = before.decode("utf-8").splitlines(keepends=True)
    new = after.decode("utf-8").splitlines(keepends=True)
    return "".join(difflib.unified_diff(old, new, fromfile="a/" + relative,
                                       tofile="b/" + relative))


def _check_unchanged(relative: str, target: Path, before: bytes | None) -> None:
    """Drift check: the file still has its recorded bytes, or is still absent (before is None)."""
    if (target.read_bytes() if target.exists() else None) != before:
        raise ValueError(f"{relative} changed before apply")


def _write_temp(folder: Path, data: bytes, prefix: str) -> Path:
    handle, name = tempfile.mkstemp(prefix=prefix, dir=folder)
    with os.fdopen(handle, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return Path(name)


def staged_apply(changes: dict[str, tuple[Path, bytes | None, bytes]],
                 backup: BackupStore, request_id: str) -> tuple[list[str], str]:
    """Apply all changes or none; a None before-state means the file is created (and removed on rollback)."""
    if not changes:
        raise ValueError("patch contains no changes")
    originals = [(relative, before) for relative, (_path, before, _after) in changes.items()]
    for relative, (target, before, _after) in changes.items():
        _check_unchanged(relative, target, before)
    staged: list[tuple[Path, Path]] = []
    replaced: list[tuple[str, Path, bytes | None]] = []
    try:
        for relative, (target, before, after) in changes.items():
            _check_unchanged(relative, target, before)
            temp = _write_temp(target.parent, after, ".lab-stage-")
            if before is not None:
                try:
                    os.chmod(temp, target.stat().st_mode)
                except OSError:
                    pass
            staged.append((temp, target))
        generation = backup.create("backup", "agent.patch", request_id, originals)
        for (relative, (target, before, _after)), (temp, _dest) in zip(changes.items(), staged):
            _check_unchanged(relative, target, before)
            os.replace(temp, target)
            replaced.append((relative, target, before))
        return [relative for relative in changes], generation.id
    except BaseException:
        failure = None
        for _relative, target, original in reversed(replaced):
            try:
                if original is None:
                    target.unlink(missing_ok=True)
                else:
                    os.replace(_write_temp(target.parent, original, ".lab-recover-"), target)
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
