"""Reviewable project patches using the mapper's validated multi-file engine."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Callable

from ..locations import CONTROL, PARTS_BIN
from .file_tools import MAX_READ
from .project_tools import ProjectTools


if str(PARTS_BIN) not in sys.path:
    sys.path.insert(0, str(PARTS_BIN))

from core.backups import BackupStore  # noqa: E402
from core.diff import DiffFile  # noqa: E402
from tools.project_patcher import ProjectPatchSession, project_patch_diff  # noqa: E402


MAX_FILES = 8
MAX_ENTRIES = 20
MAX_DIFF = 40_000

PATCH_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "patch_project_file",
            "description": "Replace one unique text block in an existing project file. Shows a diff to the browser user and applies only after approval, with a backup.",
            "parameters": {
                "type": "object", "required": ["path", "search_block", "replace_block"],
                "properties": {
                    "path": {"type": "string", "description": "Relative project file path"},
                    "search_block": {"type": "string", "description": "Exact existing text to find once"},
                    "replace_block": {"type": "string", "description": "Replacement text"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "patch_project_files",
            "description": "Patch several existing project files together. Each entry replaces one unique text block. All changes are reviewed and applied together after browser approval, with a backup.",
            "parameters": {
                "type": "object", "required": ["files"],
                "properties": {
                    "files": {"type": "array", "items": {"type": "object",
                        "required": ["path", "search_block", "replace_block"],
                        "properties": {
                            "path": {"type": "string", "description": "Relative project file path"},
                            "search_block": {"type": "string", "description": "Exact existing text to find once"},
                            "replace_block": {"type": "string", "description": "Replacement text"},
                        }}},
                },
            },
        },
    },
]


class PatchTools:
    """Validate a patch, request human approval, back up, then apply it."""

    def __init__(self, project: ProjectTools, request_approval: Callable[[dict], bool], request_id: str):
        self.project = project
        self.request_approval = request_approval
        self.request_id = request_id

    def _manifest(self, entries: object) -> dict:
        if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_ENTRIES:
            raise ValueError(f"provide 1 to {MAX_ENTRIES} patch entries")
        grouped: dict[str, dict] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("each patch entry must be an object")
            relative = entry.get("path")
            path = self.project._path(relative)
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
                grouped[key] = {"path": path.relative_to(self.project.root).as_posix(), "hunks": []}
            grouped[key]["hunks"].append({"search_block": search, "replace_block": replacement})
        return {"version": 1, "files": list(grouped.values())}

    def apply(self, entries: object) -> dict:
        if self.project.root is None:
            raise ValueError("choose a project folder in the browser first")
        manifest = self._manifest(entries)
        session = ProjectPatchSession(self.project.root, manifest)
        outcomes = session.review()
        errors = [item["error"] for item in outcomes if item["status"] == "error"]
        if errors:
            raise ValueError("; ".join(errors))
        unchanged = [item["relative_path"] for item in outcomes if item["status"] == "no_change"]
        if unchanged:
            raise ValueError("patch made no change to: " + ", ".join(unchanged))
        diff = project_patch_diff(session.results)
        newline_notes = []
        for item in outcomes:
            if item.get("final_newline_changed"):
                direction = "add" if item["patched"].endswith(("\n", "\r")) else "remove"
                newline_notes.append(f"Final newline: {direction} in {item['relative_path']}")
        if newline_notes:
            diff = ("" if diff == "(No differences)" else diff + "\n\n") + "\n".join(newline_notes)
        if len(diff) > MAX_DIFF:
            raise ValueError("patch preview is too large; split it into smaller patches")
        stats = [DiffFile(item["relative_path"], item["original"], item["patched"])
                 for item in session.results]
        summary = f"{len(stats)} file(s), +{sum(x.additions for x in stats)} / -{sum(x.deletions for x in stats)}"
        if not self.request_approval({"title": "Apply project patch?", "name": summary,
                                      "diff": diff, "paths": [x.relative_path for x in stats]}):
            return {"status": "cancelled", "message": "Project patch was cancelled; files were kept."}

        # A folder's exclusions or links may change while the approval is open.
        for entry in manifest["files"]:
            self.project._path(entry["path"])

        scope = hashlib.sha256(str(self.project.root).casefold().encode("utf-8")).hexdigest()[:16]
        store = BackupStore(CONTROL / "backups" / scope, "project", self.project.root)
        backups: list[str] = []

        def write_backup(kind: str, files):
            generation = store.create(kind, "agent.patch", self.request_id, files)
            backups.append(generation.id)
            return generation.id

        paths = session.apply_all(backup=lambda files: write_backup("backup", files),
                                  recover=lambda files: write_backup("recovery", files))
        relative_paths = [Path(path).relative_to(self.project.root).as_posix() for path in paths]
        return {"status": "patched", "message": f"Patched {len(paths)} project file(s); backup {backups[0]}",
                "paths": relative_paths, "backup": backups[0]}

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
