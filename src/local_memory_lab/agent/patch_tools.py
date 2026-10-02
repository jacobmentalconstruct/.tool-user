"""Reviewable project patches with backups and transactional application."""

from __future__ import annotations

import json
from typing import Callable

from ..locations import CONTROL
from ..workspace.backups import BackupStore
from ..workspace.patching import prepare, staged_apply
from .project_tools import ProjectTools

PATCH_TOOLS = [
    {"type": "function", "function": {"name": "patch_project_file",
     "description": "Replace one unique text block in a project file after USER approval.",
     "parameters": {"type": "object", "required": ["path", "search_block", "replace_block"],
                     "properties": {"path": {"type": "string"}, "search_block": {"type": "string"},
                                    "replace_block": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "patch_project_files",
     "description": "Patch several project files together after USER approval.",
     "parameters": {"type": "object", "required": ["files"],
                     "properties": {"files": {"type": "array", "items": {"type": "object",
                      "required": ["path", "search_block", "replace_block"],
                      "properties": {"path": {"type": "string"}, "search_block": {"type": "string"},
                                     "replace_block": {"type": "string"}}}}}}}},
]


class PatchTools:
    def __init__(self, project: ProjectTools, request_approval: Callable[[dict], bool], request_id: str,
                 on_applied: Callable[[list[str]], None] | None = None):
        self.project = project
        self.request_approval = request_approval
        self.request_id = request_id
        self.on_applied = on_applied

    def apply(self, entries: object) -> dict:
        if self.project.root is None:
            raise ValueError("choose a project folder in the browser first")
        changes, diff = prepare(self.project, entries)
        if not diff:
            raise ValueError("patch made no change")
        paths = list(changes)
        additions = sum(line.startswith("+") and not line.startswith("+++") for line in diff.splitlines())
        deletions = sum(line.startswith("-") and not line.startswith("---") for line in diff.splitlines())
        summary = f"{len(paths)} file(s), +{additions} / -{deletions}"
        proposal = {"title": "Apply project patch?", "name": summary, "diff": diff, "paths": paths}
        if not self.request_approval(proposal):
            return {"status": "cancelled", "message": "Project patch was cancelled; files were kept."}
        for relative in paths:
            self.project.path(relative)
        store = BackupStore.for_project(CONTROL, self.project.root)
        applied, backup_id = staged_apply(changes, store, self.request_id)
        if self.on_applied is not None:
            self.on_applied(applied)
        return {"status": "patched", "message": f"Patched {len(applied)} project file(s); backup {backup_id}",
                "paths": applied, "backup": backup_id}

    def call(self, name: str, arguments: object) -> dict:
        try:
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            if not isinstance(arguments, dict):
                raise ValueError("tool arguments must be an object")
            if len(json.dumps(arguments, ensure_ascii=False)) > 200_000:
                raise ValueError("patch request is too large")
            if name == "patch_project_file":
                entries = [arguments]
            elif name == "patch_project_files":
                entries = arguments.get("files")
            else:
                raise ValueError(f"unknown patch tool: {name}")
            return self.apply(entries)
        except (ValueError, OSError, TypeError, RuntimeError) as exc:
            return {"status": "error", "message": f"{name} failed: {exc}"}
