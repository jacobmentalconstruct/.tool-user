"""Small project-folder tools backed by the mapper's file and exclusion rules."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from .file_tools import MAX_CONTENT, MAX_READ, validate_name
from ..locations import PARTS_BIN


if str(PARTS_BIN) not in sys.path:
    sys.path.insert(0, str(PARTS_BIN))

from core.exclusions import ExclusionPolicy  # noqa: E402
from core.files import create_text_file  # noqa: E402
from core.paths import validate_target  # noqa: E402


PROJECT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_project",
            "description": "List the immediate files and folders inside the selected project folder or a relative subfolder. Use an empty path for the project root.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Relative folder path using /, or empty for the root"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_project_file",
            "description": "Read a small UTF-8 text file at a relative path inside the selected project.",
            "parameters": {"type": "object", "required": ["path"],
                           "properties": {"path": {"type": "string", "description": "Relative file path using /"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_project_file",
            "description": "Create a new UTF-8 text file in an existing project folder. Refuses to replace any existing file.",
            "parameters": {"type": "object", "required": ["path", "content"],
                           "properties": {"path": {"type": "string", "description": "Relative new file path using /"},
                                          "content": {"type": "string", "description": "Complete text to write"}}},
        },
    },
]


def _is_link(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


class ProjectTools:
    """One immutable project scope for one model turn."""

    def __init__(self, root: Path | None):
        self.root = root
        self.policy = ExclusionPolicy()
        if root is not None:
            self.policy.load_gitignore(root)

    @staticmethod
    def choose_root(raw_path: object) -> Path:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("Enter an existing project folder path.")
        path = validate_target(Path(raw_path.strip()).expanduser())
        if _is_link(path) or not path.is_dir():
            raise ValueError("Choose an existing direct project folder, not a link.")
        return path

    def _path(self, relative: object, *, allow_root: bool = False) -> Path:
        if self.root is None:
            raise ValueError("Choose a project folder in the browser first.")
        if not isinstance(relative, str):
            raise ValueError("path must be a relative string")
        if relative == "" and allow_root:
            return self.root
        if not relative or relative.startswith(("/", "\\")):
            raise ValueError("use a relative path inside the project")
        parts = relative.replace("\\", "/").split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise ValueError("path must not contain empty, . or .. segments")
        path = self.root
        for index, part in enumerate(parts):
            validate_name(part)
            lower = part.casefold()
            if lower in {".parts-bin", "live_control", ".env", "shared.json"} or lower.startswith(".env.") or lower.endswith((".pem", ".key")):
                raise ValueError("that project path is excluded")
            path = validate_target(path / part)
            if _is_link(path):
                raise ValueError("linked project paths cannot be used")
            is_dir = index < len(parts) - 1 or path.is_dir()
            excluded, _reason = self.policy.should_exclude_entry(path, self.root, is_dir)
            if excluded:
                raise ValueError("that project path is excluded")
        if not path.is_relative_to(self.root):
            raise ValueError("path is outside the project")
        return path

    def list_project(self, relative: object = "") -> dict:
        folder = self._path(relative, allow_root=True)
        if not folder.is_dir():
            raise ValueError("project folder does not exist")
        entries = []
        truncated = False
        with os.scandir(folder) as iterator:
            for entry in iterator:
                child = folder / entry.name
                if _is_link(child):
                    continue
                info = entry.stat(follow_symlinks=False)
                is_dir = stat.S_ISDIR(info.st_mode)
                if not is_dir and not stat.S_ISREG(info.st_mode):
                    continue
                try:
                    self._path(child.relative_to(self.root).as_posix())
                except ValueError:
                    continue
                if len(entries) == 200:
                    truncated = True
                    break
                entries.append({"name": entry.name, "type": "folder" if is_dir else "file",
                                "size": None if is_dir else info.st_size})
        entries.sort(key=lambda row: (row["type"] != "folder", row["name"].casefold(), row["name"]))
        shown = relative or "."
        return {"status": "listed", "message": f"Listed {shown}", "path": shown,
                "entries": entries, "truncated": truncated}

    def read_project_file(self, relative: object) -> dict:
        path = self._path(relative)
        if not path.is_file():
            raise ValueError("project file does not exist")
        if path.stat().st_size > MAX_READ:
            raise ValueError(f"project file is too large to read (limit {MAX_READ:,} bytes)")
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("project file is not UTF-8 text") from exc
        if "\x00" in content:
            raise ValueError("project file appears to be binary")
        return {"status": "read", "message": f"Read {relative}", "path": relative, "content": content}

    def create_project_file(self, relative: object, content: object) -> dict:
        path = self._path(relative)
        if not isinstance(content, str) or len(content) > MAX_CONTENT:
            raise ValueError(f"content must be text of at most {MAX_CONTENT:,} characters")
        if not path.parent.is_dir():
            raise ValueError("create the destination folder first")
        try:
            create_text_file(path.parent, path.name, content, extension="(None)")
        except FileExistsError as exc:
            raise ValueError("project file already exists; no changes were made") from exc
        return {"status": "created", "message": f"Created {relative} in the selected project", "path": relative}

