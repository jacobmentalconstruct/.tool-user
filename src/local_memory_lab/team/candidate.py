"""Where a role's reply goes: the pinned region of a target, and the file after the reply is written."""

from __future__ import annotations

import ast

from .regions import definitions, find_region


def region_of(source: str, target: dict) -> str:
    """The exact text a builder replaces; empty for a new function, method or file."""
    return "" if target["new"] else find_region(source, target["symbol"])


def placement(target: dict) -> str:
    """Plain words for the builder: what its reply replaces or where code inserts it."""
    symbol, new = target["symbol"], target["new"]
    if not new:
        return f"replace the region: the complete new source of {symbol}"
    if not symbol:
        return "the whole content of the new file"
    owner, _, name = symbol.rpartition(".")
    if owner:
        return f"a new method {name}, inserted at the end of class {owner}: indent it like the class's other methods"
    return f"a new top-level function {name}, appended at the end of the file"


def updated_source(source: str, target: dict, text: str) -> str:
    """The file after the candidate: the region replaced, or the new definition inserted."""
    symbol, new = target["symbol"], target["new"]
    newline = "\r\n" if "\r\n" in source else "\n"
    text = text.replace("\r\n", "\n").rstrip("\n") + "\n"  # replies often drop the final newline
    text = text.replace("\n", newline)
    if new and not symbol:
        return text
    if not new:
        return source.replace(find_region(source, symbol), text, 1)
    owner = symbol.rpartition(".")[0]
    if not owner:
        return source.rstrip("\r\n") + newline * 3 + text
    end = _end_of(source, owner)
    return source[:end] + newline + text + source[end:]


def _end_of(source: str, qualified: str) -> int:
    """Offset just after the last line of one class definition."""
    node = next(node for name, node in definitions(ast.parse(source).body)
                if name == qualified and isinstance(node, ast.ClassDef))
    lines = source.splitlines(keepends=True)
    return sum(len(line) for line in lines[:node.end_lineno])
