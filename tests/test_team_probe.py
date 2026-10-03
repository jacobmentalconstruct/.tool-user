"""The real-model team run, dry-run with stubbed roles on its throwaway project (no model, no GPU)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.bench import team_probe  # noqa: E402
from local_memory_lab.team.roles import RoleReply  # noqa: E402
import team_fixtures  # noqa: E402,F401  (blocks every model call in the default suite)

FIRST = ("def restock(levels: dict, item: str, amount: int) -> int:\n"
         "    levels[item] = levels.get(item, 0) + amount\n    return levels[item]\n")
FIXED = ("def restock(levels: dict, item: str, amount: int) -> int:\n"
         "    if amount < 1:\n        raise ValueError('amount must be at least 1')\n"
         "    levels[item] = levels.get(item, 0) + amount\n    return levels[item]\n")


def fake(config, system, payload, schema, validate=None):
    trace = {"loadSeconds": 0.1}
    if "tasks" in schema["properties"]:
        return RoleReply({"tasks": [{"title": "Implement restock", "description": "Add stock.", "check": "tests",
                                     "target": {"path": "inventory.py", "symbol": "restock", "new": False}}]},
                         "", 5, 0.1, 50.0, 1.0, trace)
    if "verdict" in schema["properties"]:
        return RoleReply({"verdict": "pass", "reasons": ["ok"], "quote": ""}, "", 5, 0.1, 50.0, 1.0, trace)
    text = FIXED if "feedback" in payload else FIRST
    return RoleReply({"replace_block": text, "notes": ""}, "", 5, 0.1, 50.0, 1.0, trace)


class TeamProbeTests(unittest.TestCase):
    def test_dry_run_reaches_done_with_a_debug_round_and_records_everything(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
            base = Path(temp)
            with patch.object(team_probe, "call_role", side_effect=fake), \
                    patch.object(team_probe, "CONTROL", base / "control"), \
                    patch("local_memory_lab.session.CONTROL", base / "control"), \
                    patch("local_memory_lab.team.jobs.CONTROL", base / "control"):
                (base / "repo" / "bench").mkdir(parents=True)
                for args in (["init", "-q"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                                              "--allow-empty", "-m", "base"]):
                    subprocess.run(["git", *args], cwd=base / "repo", check=True, capture_output=True)
                raw, summary = team_probe.run_team_probe(base / "repo", on_progress=lambda message: None,
                                                         timeout=60)
            self.assertEqual("done", summary["job"], summary.get("error"))
            self.assertEqual((40, False), (len(summary["source_commit"]), summary["source_dirty"]))
            self.assertTrue(summary["debugger_ran"])
            self.assertEqual("role:debugger", summary["origin_role"])
            self.assertTrue(summary["project_unchanged_while_pending"])
            self.assertTrue(summary["live_tests_pass"])
            self.assertTrue(summary["indexed_after_apply"])
            self.assertEqual({"verdict": "pass", "reasons": ["ok"], "correct": True}, summary["reviewer"])
            self.assertEqual(["planner", "builder", "debugger", "reviewer"], [row["role"] for row in summary["per_role"]])
            record = json.loads(next((base / "repo" / "bench" / "probes").glob("team-*.json")).read_text(encoding="utf-8"))
            self.assertIn("standing in for the USER", record["approvals_by"])
            self.assertTrue(raw.is_file())


if __name__ == "__main__":
    unittest.main()
