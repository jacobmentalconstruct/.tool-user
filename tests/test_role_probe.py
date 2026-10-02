"""The opt-in role probe, dry-run with stubbed role calls (no model or GPU)."""

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
from local_memory_lab.team.roles import RoleOutputError, RoleReply  # noqa: E402

SOURCE = 'def f(x):\n    """Doc."""\n    return x + 1\n'


def fake_call(config, system, payload, schema, validate=None):
    if "card" in payload:
        output = {"verdict": "pass", "reasons": ["Does what the task says."]}
        if config.model == "qwen3.5:35b" and "lab-probe" in payload["card"]:
            line = next(line.strip() for line in payload["card"].splitlines() if "lab-probe" in line)
            output = {"verdict": "fail", "reasons": [f"`{line}` is unrequested"]}
        validate(output)
        return RoleReply(output, "thought", 50, 1.0, 40.0)
    if payload["target_path"].endswith("context_pack.py") and "feedback" not in payload:
        raise RoleOutputError("cap", reason="cap_exhausted", thinking="long", eval_count=8192, cap_hit=True)
    return RoleReply({"replace_block": payload["region"], "notes": ""}, "thought", 100, 2.0, 50.0)


class RoleProbeTests(unittest.TestCase):
    def test_seeded_bad_candidate_adds_one_statement_after_the_docstring(self):
        seeded = roles_probe.seed_bad(SOURCE, "f", 'print("lab-probe")')
        self.assertEqual('def f(x):\n    """Doc."""\n    print("lab-probe")\n    return x + 1\n', seeded)
        ast.parse(seeded)

    def test_dry_run_records_roles_reviewer_accuracy_and_threshold(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(roles_probe, "call_role", side_effect=fake_call), \
                patch.object(roles_probe, "EMBEDDER", _BenchEmbedder):
            raw, summary = roles_probe.run_role_probe(
                ROOT, ROOT / "bench" / "tasks", output_dir=Path(temp) / "raw",
                record_dir=Path(temp) / "probes", on_progress=lambda message: None)
            self.assertEqual(1, summary["builder:qwen3.5:9b"]["invalid"])
            self.assertEqual(1, summary["builder:qwen3.5:9b"]["cap_hits"])
            self.assertEqual(0, summary["debugger:qwen3.5:9b"]["passed"])
            self.assertTrue(summary["builder_within_threshold"])
            weak, strong = summary["reviewer:qwen3.5:9b"], summary["reviewer:qwen3.5:35b"]
            self.assertGreater(strong["seeded_bad_cards"], 0)
            self.assertEqual(0, weak["seeded_bad_caught"])
            self.assertEqual(strong["seeded_bad_cards"], strong["seeded_bad_caught"])
            raw_rows = json.loads(raw.read_text(encoding="utf-8"))["rows"]
            self.assertEqual("long", next(row for row in raw_rows if not row["valid"])["thinkingExcerpt"])
            committed = json.loads(next((Path(temp) / "probes").glob("roles-*.json")).read_text(encoding="utf-8"))
            self.assertFalse(any("thinkingExcerpt" in row for row in committed["rows"]))


if __name__ == "__main__":
    unittest.main()
