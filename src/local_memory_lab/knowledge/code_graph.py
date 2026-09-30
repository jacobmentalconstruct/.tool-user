"""Small AST code graph for definitions, imports, calls and test links."""

from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass(frozen=True)
class GraphNode:
    key: str
    path: str
    name: str
    kind: str
    line: int


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    kind: str


class _GraphVisitor(ast.NodeVisitor):
    def __init__(self, path: str):
        self.path = path
        self.nodes: dict[str, GraphNode] = {}
        self.edges: set[GraphEdge] = set()
        self.owner = [f"file:{path}"]
        self.scope: list[str] = []

    @staticmethod
    def _name(node: ast.AST) -> str:
        try:
            return ast.unparse(node)
        except (AttributeError, TypeError):
            return "unknown"

    def _reference(self, name: str) -> str:
        key = f"symbol:{name}"
        self.nodes.setdefault(key, GraphNode(key, "", name, "symbol", 0))
        return key

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            target = self._reference(item.name)
            self.edges.add(GraphEdge(self.owner[-1], target, "imports"))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = "." * node.level + (node.module or "")
        if module:
            target = self._reference(module)
            self.edges.add(GraphEdge(self.owner[-1], target, "imports"))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_definition(node, "class")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_definition(node, "function")

    visit_AsyncFunctionDef = visit_FunctionDef

    def _visit_definition(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
                          kind: str) -> None:
        qualified = ".".join([*self.scope, node.name])
        key = f"definition:{self.path}:{qualified}"
        self.nodes[key] = GraphNode(key, self.path, qualified, kind, node.lineno)
        self.edges.add(GraphEdge(self.owner[-1], key, "defines"))
        self.owner.append(key)
        self.scope.append(node.name)
        for child in node.body:
            self.visit(child)
        self.scope.pop()
        self.owner.pop()

    def visit_Call(self, node: ast.Call) -> None:
        name = self._name(node.func)
        target = self._reference(name)
        self.edges.add(GraphEdge(self.owner[-1], target, "calls"))
        self.generic_visit(node)


def build_graph(path: str, content: str) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Build source-file relationships without importing or executing the file."""
    file_key = f"file:{path}"
    visitor = _GraphVisitor(path)
    visitor.nodes[file_key] = GraphNode(file_key, path, path, "file", 1)
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return list(visitor.nodes.values()), []
    visitor.visit(tree)
    if path.replace("\\", "/").startswith("tests/"):
        for edge in tuple(visitor.edges):
            if edge.kind == "imports" and edge.target.startswith("symbol:local_memory_lab"):
                visitor.edges.add(GraphEdge(file_key, edge.target, "tests"))
    return list(visitor.nodes.values()), list(visitor.edges)
