"""Import-boundary checks for the application package."""

from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))


def imports_for(path: Path, module: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                package = module.split(".")[:-1]
                keep = max(0, len(package) - node.level + 1)
                base = package[:keep]
                if node.module:
                    base.extend(node.module.split("."))
                if base:
                    result.add(".".join(base))
            elif node.module:
                result.add(node.module)
    return result


WRITE_CALLS = {"write_text", "write_bytes", "unlink", "rmtree", "copy2", "copytree", "copyfile", "mkdir",
               "rename", "fdopen"}
# Only workspace/ writes project files. These modules write runtime state under live_control/ only,
# and bench/ works on temporary copies of a pinned snapshot, never on a selected project.
RUNTIME_WRITERS = {"event_store.py", "knowledge/store.py", "interfaces/web.py", "interfaces/launcher.py"}


def writes_files(tree: ast.AST) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            mode = next((arg.value for arg in node.args[1:2] if isinstance(arg, ast.Constant)), "")
            mode = next((kw.value.value for kw in node.keywords if kw.arg == "mode"
                         and isinstance(kw.value, ast.Constant)), mode)
            owner = getattr(getattr(node.func, "value", None), "id", "")
            if (name in WRITE_CALLS or (name == "replace" and owner == "os")
                    or (name == "open" and isinstance(mode, str) and set(mode) & set("wax+"))):
                lines.append(node.lineno)
    return lines


class ArchitectureTests(unittest.TestCase):
    def test_only_the_workspace_writes_project_files(self):
        for path in SRC.rglob("*.py"):
            relative = path.relative_to(SRC / "local_memory_lab").as_posix()
            if relative.startswith(("workspace/", "bench/")) or relative in RUNTIME_WRITERS:
                continue
            lines = writes_files(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
            self.assertEqual([], lines, f"{relative} writes files; only workspace/ may write project files")

    def test_chat_sends_no_tools(self):
        from unittest.mock import patch
        from local_memory_lab.agent import chat
        with patch.object(chat, "ollama_json", return_value={"message": {"content": "hi"}}) as call:
            chat.answer("hello", "m", [], [])
        payload = call.call_args.args[1]
        self.assertNotIn("tools", payload)
        self.assertEqual(["system", "user"], [message["role"] for message in payload["messages"]])

    def test_source_names_no_location_outside_the_repo(self):
        outside = re.compile(r"^[A-Za-z]:[\\/]|^~|(\.\.[\\/]){2}|_SANDBOX")
        for path in SRC.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self.assertIsNone(outside.search(node.value), f"{path}: {node.value[:60]!r}")
                if isinstance(node, ast.Attribute) and node.attr == "path" and isinstance(node.value, ast.Name):
                    self.assertNotEqual("sys", node.value.id, f"{path} changes sys.path")


    def test_no_reference_project_import_or_path_injection(self):
        for path in SRC.rglob("*.py"):
            text = path.read_text(encoding="utf-8").casefold()
            self.assertNotIn(".parts" + "-bin", text, path)
            module = "local_memory_lab" + "." + ".".join(path.relative_to(SRC).with_suffix("").parts)
            for imported in imports_for(path, module):
                root = imported.split(".", 1)[0]
                self.assertNotIn(root, {"core", "tools"}, path)
                self.assertTrue(root in sys.stdlib_module_names or root in {"local_memory_lab", "numpy"},
                                f"non-standard dependency {imported} in {path}")
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr in {"insert", "append"} and isinstance(node.func.value, ast.Attribute):
                        if node.func.value.attr == "path":
                            self.assertFalse(any(isinstance(child, ast.Constant) and
                                                 isinstance(child.value, str) and (".parts" + "-bin") in child.value.casefold()
                                                 for child in ast.walk(node)), path)

    def test_no_import_cycles_and_core_does_not_import_interfaces(self):
        files = {"local_memory_lab." + ".".join(p.relative_to(SRC).with_suffix("").parts): p
                 for p in SRC.rglob("*.py")}
        graph = {module: {target for target in imports_for(path, module) if target in files}
                 for module, path in files.items()}
        for module, targets in graph.items():
            if not module.startswith("local_memory_lab.interfaces"):
                self.assertFalse(any(target.startswith("local_memory_lab.interfaces") for target in targets), module)
        visiting, visited = set(), set()

        def visit(module):
            if module in visiting:
                self.fail(f"import cycle includes {module}")
            if module in visited:
                return
            visiting.add(module)
            for target in graph[module]:
                visit(target)
            visiting.remove(module)
            visited.add(module)

        for module in graph:
            visit(module)


if __name__ == "__main__":
    unittest.main()
