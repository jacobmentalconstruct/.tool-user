"""Import-boundary checks for the application package."""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


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


class ArchitectureTests(unittest.TestCase):
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
                                                 isinstance(child.value, str) and ".parts-bin" in child.value.casefold()
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
