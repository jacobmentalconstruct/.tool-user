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


def _decorated_start(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> int:
    return min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])


def _class_chunks(path: str, lines: list[str], node: ast.ClassDef) -> tuple[list[Chunk], list[str]]:
    methods = [child for child in node.body
               if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))]
    class_start = _decorated_start(node)
    class_end = node.end_lineno or node.lineno
    header_end = _decorated_start(methods[0]) - 1 if methods else class_end
    chunks = [_chunk(path, lines, class_start, max(class_start, header_end), "class_header")]
    names = [f"class {node.name}"]
    method_nodes = set(methods)
    for child in node.body:
        if child in method_nodes:
            start = _decorated_start(child)
            end = child.end_lineno or child.lineno
            chunks.append(_chunk(path, lines, start, end, "method"))
            names.append(f"method {node.name}.{child.name}")
        elif child.lineno > header_end:
            chunks.append(_chunk(path, lines, child.lineno,
                                 child.end_lineno or child.lineno, "class_member"))
    return chunks, names


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
            if isinstance(node, ast.ClassDef):
                class_chunks, class_names = _class_chunks(path, lines, node)
                chunks.extend(class_chunks)
                definitions.extend(class_names)
            else:
                chunks.append(_chunk(path, lines, _decorated_start(node),
                                     node.end_lineno or node.lineno, "function"))
                definitions.append(f"function {node.name}")
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
    headings = []
    fence: tuple[str, int] | None = None
    for index, line in enumerate(lines, 1):
        fence_match = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence is not None:
            marker, length = fence
            if re.match(rf"^ {{0,3}}{re.escape(marker)}{{{length},}}\s*$", line):
                fence = None
            continue
        if fence_match:
            token = fence_match.group(1)
            fence = (token[0], len(token))
            continue
        if re.match(r"^ {0,3}#{1,6}\s+\S", line):
            headings.append(index)
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
