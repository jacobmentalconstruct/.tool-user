"""One agent-facing catalogue over the sandbox, project, and patch components."""

from __future__ import annotations

import json
from pathlib import Path

from .file_tools import TOOLS as FILE_TOOLS, FileTools
from .patch_tools import PATCH_TOOLS, PatchTools
from .project_tools import PROJECT_TOOLS, ProjectTools


class SharedTools:
    schemas = FILE_TOOLS + PROJECT_TOOLS + PATCH_TOOLS

    def __init__(self, file_tools: FileTools, project_root: Path | None,
                 request_patch_approval, request_id: str):
        self.file_tools = file_tools
        self.project = ProjectTools(project_root)
        self.patches = PatchTools(self.project, request_patch_approval, request_id)
        self.system_hint = (
            "The browser user chooses a project folder. Use list_project and read_project_file "
            "to inspect it. create_project_file only creates a new file. For changes to existing "
            "project files, use patch_project_file or patch_project_files with unique search blocks "
            "from files you have read. The browser user reviews a diff and approves or cancels. "
            "If cancelled, do not retry the same patch this turn. The original write_file, "
            "read_file, and list_files tools still use the app's separate files folder. "
            "Never claim a file changed unless the tool reports success."
            + (f" The selected project root is {project_root}." if project_root else
               " No project folder is selected yet; ask the browser user to choose one first.")
        )

    def call(self, name: str, arguments: object, confirm_overwrite) -> dict:
        if name in {"patch_project_file", "patch_project_files"}:
            return self.patches.call(name, arguments)
        if name not in {"list_project", "read_project_file", "create_project_file"}:
            return self.file_tools.call(name, arguments, confirm_overwrite)
        try:
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            if not isinstance(arguments, dict):
                raise ValueError("tool arguments must be an object")
            if name == "list_project":
                return self.project.list_project(arguments.get("path", ""))
            if name == "read_project_file":
                return self.project.read_project_file(arguments.get("path"))
            return self.project.create_project_file(arguments.get("path"), arguments.get("content"))
        except (ValueError, OSError, TypeError) as exc:
            return {"status": "error", "message": f"{name} failed: {exc}"}
