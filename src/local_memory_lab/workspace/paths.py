"""Project path validation, exclusions, and bounded text file operations."""

from __future__ import annotations

import fnmatch
import os
import re
import stat
from pathlib import Path

MAX_CONTENT = 100_000
MAX_READ = 100_000
MAX_NAME = 120
MAX_ENTRIES = 200
EXCLUDED_NAMES = {".git", ".hg", ".svn", "live_control", ".lab", "shared.json"}


def validate_name(name: object) -> str:
    if not isinstance(name, str) or not name or len(name) > MAX_NAME:
        raise ValueError(f"name must be a nonempty file name of at most {MAX_NAME} characters")
    if name in {".", ".."} or name.endswith((" ", ".")) or re.search(r'[<>:"/\\|?*\x00-\x1f]', name):
        raise ValueError("name is not a valid file name")
    stem = name.split(".", 1)[0]
    if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])", stem):
        raise ValueError("name is reserved by Windows")
    return name


def is_link(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def choose_root(raw_path: object) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("Enter an existing project folder path.")
    root = Path(raw_path.strip()).expanduser().absolute()
    if is_link(root) or not root.is_dir():
        raise ValueError("Choose an existing direct project folder, not a link.")
    return root.resolve()


def _gitignore_rules(root: Path) -> list[tuple[str, bool, bool, bool]]:
    rules = []
    ignore_file = root / ".gitignore"
    if is_link(ignore_file):
        return rules
    try:
        lines = ignore_file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return rules
    for raw in lines:
        rule = raw.strip()
        if not rule or rule.startswith("#"):
            continue
        negated = rule.startswith("!")
        if negated:
            rule = rule[1:]
        anchored = rule.startswith("/")
        directory_only = rule.endswith("/")
        rule = rule.strip("/")
        rules.append((rule, negated, anchored or "/" in rule, directory_only))
    return rules


def excluded(root: Path, path: Path, is_dir: bool,
             rules: list[tuple[str, bool, bool, bool]] | None = None) -> bool:
    relative = path.relative_to(root).as_posix()
    parts = relative.split("/")
    for part in parts:
        lower = part.casefold()
        if (lower in EXCLUDED_NAMES or fnmatch.fnmatchcase(lower, ".*-bin") or
                lower == ".env" or lower.startswith(".env.") or lower.endswith((".pem", ".key"))):
            return True
    ignored = False
    for pattern, negated, anchored, directory_only in rules if rules is not None else _gitignore_rules(root):
        candidate = relative if anchored else parts[-1]
        if fnmatch.fnmatchcase(candidate, pattern) and (not directory_only or is_dir):
            ignored = not negated
    return ignored


class Workspace:
    def __init__(self, root: Path | None):
        self.root = Path(root).resolve() if root is not None else None
        self.rules = _gitignore_rules(self.root) if self.root else []

    def path(self, relative: object, *, allow_root: bool = False) -> Path:
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
            path = path / part
            if is_link(path):
                raise ValueError("linked project paths cannot be used")
            if excluded(self.root, path, index < len(parts) - 1 or path.is_dir(), self.rules):
                raise ValueError("that project path is excluded")
        if not path.resolve(strict=False).is_relative_to(self.root):
            raise ValueError("path is outside the project")
        return path

    def list_project(self, relative: object = "") -> dict:
        folder = self.path(relative, allow_root=True)
        if not folder.is_dir():
            raise ValueError("project folder does not exist")
        entries, truncated = [], False
        with os.scandir(folder) as iterator:
            for entry in iterator:
                child = folder / entry.name
                if is_link(child):
                    continue
                info = entry.stat(follow_symlinks=False)
                is_dir = stat.S_ISDIR(info.st_mode)
                if not is_dir and not stat.S_ISREG(info.st_mode):
                    continue
                try:
                    self.path(child.relative_to(self.root).as_posix())
                except ValueError:
                    continue
                if len(entries) == MAX_ENTRIES:
                    truncated = True
                    break
                entries.append({"name": entry.name, "type": "folder" if is_dir else "file",
                                "size": None if is_dir else info.st_size})
        entries.sort(key=lambda item: (item["type"] != "folder", item["name"].casefold(), item["name"]))
        shown = relative or "."
        return {"status": "listed", "message": f"Listed {shown}", "path": shown,
                "entries": entries, "truncated": truncated}

    def read_project_file(self, relative: object) -> dict:
        path = self.path(relative)
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
        path = self.path(relative)
        if not isinstance(content, str) or len(content) > MAX_CONTENT:
            raise ValueError(f"content must be text of at most {MAX_CONTENT:,} characters")
        if not path.parent.is_dir():
            raise ValueError("create the destination folder first")
        try:
            with path.open("x", encoding="utf-8", newline="") as stream:
                stream.write(content)
        except FileExistsError as exc:
            raise ValueError("project file already exists; no changes were made") from exc
        return {"status": "created", "message": f"Created {relative} in the selected project", "path": relative}
