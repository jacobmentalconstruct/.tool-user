"""Small, bounded file tools. Inspired by the supplied project mapper's file actions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path


MAX_CONTENT = 100_000
MAX_READ = 100_000

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a UTF-8 text file with the given name and content. An existing file requires user approval before overwrite.",
            "parameters": {
                "type": "object",
                "required": ["name", "content"],
                "properties": {
                    "name": {"type": "string", "description": "One file name, such as HelloWORLD.md; no folders or paths"},
                    "content": {"type": "string", "description": "The complete text to place inside the file"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file from the app's files folder.",
            "parameters": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string", "description": "One existing file name"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List file names in the app's files folder.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def validate_name(name: object) -> str:
    if not isinstance(name, str) or not name or len(name) > 120:
        raise ValueError("name must be a nonempty file name of at most 120 characters")
    if name in {".", ".."} or name.endswith((" ", ".")):
        raise ValueError("name is not a valid file name")
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', name):
        raise ValueError("name must be one file name, without a path or reserved characters")
    stem = name.split(".", 1)[0]
    if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])", stem):
        raise ValueError("name is reserved by Windows")
    return name


def _result(status: str, message: str, **data) -> dict:
    return {"status": status, "message": message, **data}


class FileTools:
    def __init__(self, folder: Path):
        self.folder = Path(folder)

    def _target(self, name: object) -> Path:
        name = validate_name(name)
        if self.folder.is_symlink():
            raise ValueError("files folder is a link; choose a direct folder")
        target = self.folder / name
        if target.is_symlink():
            raise ValueError("linked files cannot be used")
        return target

    def write_file(self, name: object, content: object, confirm_overwrite) -> dict:
        target = self._target(name)
        if not isinstance(content, str) or len(content) > MAX_CONTENT:
            raise ValueError(f"content must be text of at most {MAX_CONTENT:,} characters")
        self.folder.mkdir(exist_ok=True)
        try:
            with target.open("x", encoding="utf-8", newline="") as stream:
                stream.write(content)
            return _result("created", f"Created {target}", name=name)
        except FileExistsError:
            pass

        if target.is_symlink() or not target.is_file():
            raise ValueError("the target is not a regular file")
        previous = target.read_bytes()
        old_hash = hashlib.sha256(previous).digest()
        preview = previous[:4000].decode("utf-8", errors="replace")
        if len(previous) > 4000:
            preview += "\n… (preview truncated)"
        if not confirm_overwrite(name, preview, content):
            return _result("cancelled", f"Cancelled overwrite of {name}; existing content was kept.", name=name)

        if target.is_symlink() or not target.is_file() or hashlib.sha256(target.read_bytes()).digest() != old_hash:
            return _result("changed", f"{name} changed while approval was open; nothing was written.", name=name)
        scratch = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.folder, prefix=".agent-write-", delete=False) as stream:
                scratch = Path(stream.name)
                stream.write(content.encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(scratch, stat.S_IMODE(target.stat().st_mode))
            os.replace(scratch, target)
            return _result("overwritten", f"Overwrote {target}", name=name)
        finally:
            if scratch is not None:
                scratch.unlink(missing_ok=True)

    def read_file(self, name: object) -> dict:
        target = self._target(name)
        if not target.is_file():
            raise ValueError(f"{name} does not exist")
        if target.stat().st_size > MAX_READ:
            raise ValueError(f"{name} is too large to read with this tool")
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{name} is not UTF-8 text") from exc
        return _result("read", f"Read {name}", name=name, content=content)

    def list_files(self) -> dict:
        if self.folder.is_symlink():
            raise ValueError("files folder is a link")
        if not self.folder.exists():
            return _result("listed", "No files yet.", files=[])
        files = sorted(path.name for path in self.folder.iterdir() if path.is_file() and not path.is_symlink())
        return _result("listed", f"Found {len(files)} file(s).", files=files[:200], truncated=len(files) > 200)

    def call(self, name: str, arguments: object, confirm_overwrite) -> dict:
        try:
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            if not isinstance(arguments, dict):
                raise ValueError("tool arguments must be an object")
            if name == "write_file":
                return self.write_file(arguments.get("name"), arguments.get("content"), confirm_overwrite)
            if name == "read_file":
                return self.read_file(arguments.get("name"))
            if name == "list_files":
                return self.list_files()
            raise ValueError(f"unknown tool: {name}")
        except (ValueError, OSError, TypeError) as exc:
            return _result("error", f"{name} failed: {exc}")
