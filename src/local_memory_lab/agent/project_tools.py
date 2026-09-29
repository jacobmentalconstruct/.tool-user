"""Agent-facing project tools backed by the workspace component."""

from __future__ import annotations

from pathlib import Path

from ..workspace.paths import Workspace, choose_root

PROJECT_TOOLS = [
    {"type": "function", "function": {"name": "list_project",
     "description": "List files and folders in the selected project.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "read_project_file",
     "description": "Read a small UTF-8 text file in the selected project.",
     "parameters": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "create_project_file",
     "description": "Create a new UTF-8 text file in the selected project.",
     "parameters": {"type": "object", "required": ["path", "content"],
                     "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}}},
]


class ProjectTools(Workspace):
    """One immutable project scope for one model turn."""

    @staticmethod
    def choose_root(raw_path: object) -> Path:
        return choose_root(raw_path)
