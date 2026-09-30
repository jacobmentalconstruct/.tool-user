"""Task metadata and safe function-body hole punching for the bench."""

from __future__ import annotations

import ast


def _functions(body: list[ast.stmt], scope: tuple[str, ...] = ()):
    for node in body:
        if isinstance(node, ast.ClassDef):
            yield from _functions(node.body, (*scope, node.name))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield ".".join((*scope, node.name)), node
            yield from _functions(node.body, (*scope, node.name))


def punch_function(source: str, qualified_name: str, marker: str = "bench task") -> tuple[str, str]:
    """Replace one Python function implementation while retaining its interface."""
    tree = ast.parse(source)
    matches = [node for name, node in _functions(tree.body) if name == qualified_name]
    if len(matches) != 1:
        raise ValueError(f"expected one function named {qualified_name!r}")
    node = matches[0]
    body = node.body
    doc = body[0] if (isinstance(body[0], ast.Expr) and
                      isinstance(body[0].value, ast.Constant) and
                      isinstance(body[0].value.value, str)) else None
    first_impl = body[1] if doc else body[0]
    if first_impl.lineno == node.lineno:
        raise ValueError("one-line function bodies cannot be hole-punched")
    lines = source.splitlines(keepends=True)
    start, end = first_impl.lineno - 1, node.end_lineno
    removed = "".join(lines[start:end])
    if not removed.strip():
        raise ValueError("function implementation body is empty")
    indent = " " * first_impl.col_offset
    newline = "\r\n" if "\r\n" in lines[start] else "\n"
    replacement = f'{indent}raise NotImplementedError({marker!r}){newline}'
    punched = "".join((*lines[:start], replacement, *lines[end:]))
    return punched, removed
