"""Locate one Python definition so code, not a model, pins the region a task edits."""

from __future__ import annotations

import ast


def definitions(body: list[ast.stmt], scope: tuple[str, ...] = ()):
    """Yield every function, method and class with its dotted qualified name."""
    for node in body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            yield ".".join((*scope, node.name)), node
            yield from definitions(node.body, (*scope, node.name))


def find_region(source: str, symbol: str) -> str:
    """Return the exact source of one definition, decorators included."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(f"target file is not valid Python: {exc.msg}") from exc
    matches = [node for name, node in definitions(tree.body) if name == symbol]
    if len(matches) != 1:
        raise ValueError(f"symbol {symbol!r} must resolve to exactly one definition "
                         f"(found {len(matches)})")
    node = matches[0]
    first = min([node.lineno, *(item.lineno for item in node.decorator_list)])
    lines = source.splitlines(keepends=True)
    start = sum(len(line) for line in lines[:first - 1])
    end = sum(len(line) for line in lines[:node.end_lineno])
    return source[start:end]
