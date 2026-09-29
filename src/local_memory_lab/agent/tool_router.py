"""One agent-facing catalogue for workspace inspection and reviewed patches."""

from __future__ import annotations

import json
from pathlib import Path

from .patch_tools import PATCH_TOOLS, PatchTools
from .project_tools import PROJECT_TOOLS, ProjectTools


class SharedTools:
    schemas = PROJECT_TOOLS + PATCH_TOOLS

    def __init__(self, project_root: Path | None, request_patch_approval, request_id: str):
        self.project = ProjectTools(project_root)
        self.patches = PatchTools(self.project, request_patch_approval, request_id)
        self.system_hint = (
            "Use list_project and read_project_file to inspect the selected project. "
            "create_project_file only creates a new file. For changes to existing files, "
            "use patch_project_file or patch_project_files with unique text you have read. "
            "The USER reviews each diff and approves or cancels it. Never claim a change "
            "unless the tool reports success."
            + (f" Selected project root: {project_root}." if project_root else
               " No project is selected; ask the USER to choose one first.")
        )

    def call(self, name: str, arguments: object) -> dict:
        if name in {"patch_project_file", "patch_project_files"}:
            return self.patches.call(name, arguments)
        try:
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            if not isinstance(arguments, dict):
                raise ValueError("tool arguments must be an object")
            if name == "list_project":
                return self.project.list_project(arguments.get("path", ""))
            if name == "read_project_file":
                return self.project.read_project_file(arguments.get("path"))
            if name == "create_project_file":
                return self.project.create_project_file(arguments.get("path"), arguments.get("content"))
            raise ValueError(f"unknown tool: {name}")
        except (ValueError, OSError, TypeError) as exc:
            return {"status": "error", "message": f"{name} failed: {exc}"}
