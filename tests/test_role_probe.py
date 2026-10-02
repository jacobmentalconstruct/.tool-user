"""The role probe, dry-run on a tiny fixture with stubbed role calls (no git, model or GPU)."""

from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.bench import roles_probe  # noqa: E402
from local_memory_lab.bench.harness import _BenchEmbedder  # noqa: E402
from local_memory_lab.team.roles import RoleOutputError, RoleReply, load_roles  # noqa: E402

SOURCE = 'def f(x):\n    """Doc."""\n    return x + 1\n'
FIXTURE = {
    "calc.py": 'def add(a, b):\n    """Add two numbers."""\n    total = a + b\n    return total\n',
    "tests/test_calc.py": ("import sys\nimport unittest\nfrom pathlib import Path\n\n"
                           "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
                           "from calc import add  # noqa: E402\n\n\n"
                           "class CalcTests(unittest.TestCase):\n"
                           "    def test_add(self):\n        self.assertEqual(5, add(2, 3))\n"),
}
TASK = {"id": "fix-001", "source": "self@0000000", "goal": "Add two numbers.",
        "target": {"path": "calc.py", "function": "add"}, "gold_files": ["calc.py"],
        "check": ["python", "-B", "-m", "unittest", "test_calc.CalcTests.test_add"],
        "synthetic_reason": None}


def fake_call(config, system, payload, schema, validate=None):
    if "card" in payload:
        output = {"verdict": "pass", "reasons": ["Does what the task says."]}
        if config.model == "qwen3.5:35b" and "lab-probe" in payload["card"]:
            line = next(line.strip() for line in payload["card"].splitlines() if "lab-probe" in line)
            output = {"verdict": "fail", "reasons": [f"`{line}` is unrequested"]}
        validate(output)
        return RoleReply(output, "thought", 50, 1.0, 40.0)
    if "feedback" not in payload:
        raise RoleOutputError("cap", reason="cap_exhausted", thinking="long", eval_count=8192, cap_hit=True)
    fixed = payload["region"].replace("    raise NotImplementedError('fix-001')\n", "    return a + b\n")
    return RoleReply({"replace_block": fixed, "notes": ""}, "thought", 100, 2.0, 50.0)


class RoleProbeTests(unittest.TestCase):
    def test_seeded_bad_candidate_adds_one_statement_after_the_docstring(self):
        seeded = roles_probe.seed_bad(SOURCE, "f", 'print("lab-probe")')
        self.assertEqual('def f(x):\n    """Doc."""\n    print("lab-probe")\n    return x + 1\n', seeded)
        ast.parse(seeded)

    def test_dry_run_records_roles_reviewer_accuracy_and_threshold(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(roles_probe, "call_role", side_effect=fake_call), \
                patch.object(roles_probe, "EMBEDDER", _BenchEmbedder):
            base = Path(temp)
            snapshot = base / "snapshot"
            for relative, text in FIXTURE.items():
                (snapshot / relative).parent.mkdir(parents=True, exist_ok=True)
                (snapshot / relative).write_bytes(text.encode("utf-8"))
            (base / "work").mkdir()
            roles = load_roles(ROOT / "roles.json")
            rows = roles_probe.probe_roles(roles, [TASK], snapshot, base / "work",
                                           on_progress=lambda message: None)
            raw, summary = roles_probe.record_probe(
                rows, roles, [TASK], repo_root=base / "repo", context_budget=6000,
                source={"source_commit": "abc", "source_dirty": False},
                output_dir=base / "raw", record_dir=base / "probes")
            self.assertEqual((1, 1), (summary["builder:qwen3.5:9b"]["invalid"],
                                      summary["builder:qwen3.5:9b"]["cap_hits"]))
            self.assertEqual(1, summary["debugger:qwen3.5:9b"]["passed"])
            self.assertTrue(summary["builder_within_threshold"])
            weak, strong = summary["reviewer:qwen3.5:9b"], summary["reviewer:qwen3.5:35b"]
            self.assertEqual((1, 0, 1), (weak["seeded_bad_cards"], weak["seeded_bad_caught"], weak["correct"]))
            self.assertEqual((1, 2), (strong["seeded_bad_caught"], strong["correct"]))
            raw_rows = json.loads(raw.read_text(encoding="utf-8"))["rows"]
            self.assertEqual("long", next(row for row in raw_rows if not row["valid"])["thinkingExcerpt"])
            committed = json.loads(next((base / "probes").glob("roles-*.json")).read_text(encoding="utf-8"))
            self.assertEqual("abc", committed["source_commit"])
            self.assertFalse(any("thinkingExcerpt" in row for row in committed["rows"]))


if __name__ == "__main__":
    unittest.main()
