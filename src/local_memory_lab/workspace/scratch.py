"""Disposable task workspaces: candidates live here until the USER approves (D16)."""

from __future__ import annotations

import shutil
from pathlib import Path

from .paths import Workspace

MAX_COPY_FILES = 20_000
MAX_COPY_BYTES = 500_000_000


class TaskWorkspace:
    """A copy of the selected project plus the planned files' bytes at copy time."""

    def __init__(self, project_root: Path, root: Path, before: dict[str, bytes | None]):
        self.project = Workspace(project_root)
        self.root = Path(root)
        self.copy = Workspace(self.root)
        self.before = before

    @classmethod
    def create(cls, project_root: Path, destination: Path, planned: list[str]) -> "TaskWorkspace":
        project = Workspace(Path(project_root).resolve(strict=True))
        destination = Path(destination).resolve()
        if destination.is_relative_to(project.root) or project.root.is_relative_to(destination):
            raise ValueError("a task workspace must be outside the selected project")
        if destination.exists():
            raise ValueError("task workspace already exists")
        for relative in planned:
            project.path(relative)  # validate every planned path before copying
        try:
            _copy_tree(project, destination)
        except BaseException:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        # before-bytes come from the copy itself, so the candidate's diff is always against its base;
        # the drift check at apply still compares them with the live project (reviewer note L2)
        copy = Workspace(destination)
        before = {relative: copy.path(relative).read_bytes() if copy.path(relative).is_file() else None
                  for relative in planned}
        return cls(project.root, destination, before)

    def path(self, relative: str) -> Path:
        if relative not in self.before:
            raise ValueError("path is outside the task's files")
        return self.copy.path(relative)

    def changes(self) -> dict[str, tuple[Path, bytes | None, bytes | None]]:
        """Changed planned files as (live path, bytes at copy time, candidate bytes)."""
        result = {}
        for relative, before in self.before.items():
            candidate = self.copy.path(relative)
            after = candidate.read_bytes() if candidate.is_file() else None
            if after != before:
                result[relative] = (self.project.path(relative), before, after)
        return result

    def discard(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def _copy_tree(project: Workspace, destination: Path) -> None:
    files = size = 0
    destination.mkdir(parents=True)
    for relative, source in project.files():
        files += 1
        size += source.stat().st_size
        if files > MAX_COPY_FILES or size > MAX_COPY_BYTES:
            raise ValueError("project is too large for a task workspace")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
