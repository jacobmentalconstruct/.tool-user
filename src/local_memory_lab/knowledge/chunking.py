"""Deterministic source chunks and summaries; no model calls."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from ..workspace.paths import Workspace


@dataclass(frozen=True)
class Chunk:
    path: str
    lines: tuple[int, int]
    kind: str
    text: str


def _chunk(path: str, lines: list[str], start: int, end: int, kind: str) -> Chunk:
    return Chunk(path, (start, end), kind, "\n".join(lines[start - 1:end]))


def _python_chunks(path: str, content: str) -> tuple[list[Chunk], str]:
    lines = content.splitlines()
    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        chunks = [_chunk(path, lines, 1, len(lines), "source")] if lines else []
        return chunks, f"Python source with a syntax error at line {exc.lineno}; {len(lines)} lines."

    chunks: list[Chunk] = []
    definitions: list[str] = []
    imports: list[ast.stmt] = []
    other: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if other:
                chunks.append(_chunk(path, lines, other[0].lineno,
                                     other[-1].end_lineno or other[-1].lineno, "module"))
                other = []
            imports.append(node)
            continue
        if imports:
            chunks.append(_chunk(path, lines, imports[0].lineno,
                                 imports[-1].end_lineno or imports[-1].lineno, "imports"))
            imports = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if other:
                chunks.append(_chunk(path, lines, other[0].lineno,
                                     other[-1].end_lineno or other[-1].lineno, "module"))
                other = []
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            start = min([node.lineno, *(part.lineno for part in node.decorator_list)])
            chunks.append(_chunk(path, lines, start, node.end_lineno or node.lineno, kind))
            definitions.append(f"{kind} {node.name}")
        else:
            other.append(node)
    if imports:
        chunks.append(_chunk(path, lines, imports[0].lineno,
                             imports[-1].end_lineno or imports[-1].lineno, "imports"))
    if other:
        chunks.append(_chunk(path, lines, other[0].lineno,
                             other[-1].end_lineno or other[-1].lineno, "module"))
    docstring = ast.get_docstring(tree) or ""
    description = docstring.splitlines()[0] if docstring else "Python module"
    summary = description + ("; " + ", ".join(definitions[:12]) if definitions else "")
    return chunks, summary


def _markdown_chunks(path: str, content: str) -> tuple[list[Chunk], str]:
    lines = content.splitlines()
    headings = [index for index, line in enumerate(lines, 1)
                if re.match(r"^#{1,6}\s+\S", line)]
    chunks: list[Chunk] = []
    if headings:
        if headings[0] > 1 and any(line.strip() for line in lines[:headings[0] - 1]):
            chunks.append(_chunk(path, lines, 1, headings[0] - 1, "paragraph"))
        for position, start in enumerate(headings):
            end = headings[position + 1] - 1 if position + 1 < len(headings) else len(lines)
            chunks.append(_chunk(path, lines, start, end, "section"))
        summary = "; ".join(lines[index - 1].lstrip("# ").strip() for index in headings[:6])
        return chunks, summary

    start = None
    for index, line in enumerate([*lines, ""], 1):
        if line.strip() and start is None:
            start = index
        elif not line.strip() and start is not None:
            chunks.append(_chunk(path, lines, start, index - 1, "paragraph"))
            start = None
    summary = " ".join(lines).strip()[:240] or "Empty Markdown file"
    return chunks, summary


def chunk_file(workspace: Workspace, relative: str) -> tuple[list[Chunk], str]:
    """Read one allowed project file and return chunks with its short summary."""
    path = workspace.path(relative)
    suffix = path.suffix.casefold()
    if suffix not in {".py", ".md", ".markdown"}:
        raise ValueError("only Python and Markdown files can be chunked")
    content = workspace.read_project_file(relative)["content"]
    if suffix == ".py":
        return _python_chunks(relative, content)
    return _markdown_chunks(relative, content)
