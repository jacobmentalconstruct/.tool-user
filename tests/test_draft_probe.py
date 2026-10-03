"""The goal-draft probe, dry-run with stubbed model calls on an archive of this repo (no model, no GPU)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.bench import draft_probe  # noqa: E402
from local_memory_lab.team.roles import RoleReply  # noqa: E402
import team_fixtures  # noqa: E402,F401  (blocks every model call in the default suite)


def fake_draft(config, system, payload, schema, **kwargs):
    """Drafts the expected target for G1, and the G3 confusion (a new function as a new file) otherwise."""
    if "require_citation" in payload["conversation"]:
        out = {"goal": "In team/steps.py, make require_citation reject quote parts shorter than CITE_MIN.",
               "target": {"path": "src/local_memory_lab/team/steps.py", "shape": "existing_symbol",
                          "symbol": "require_citation"}}
    else:
        out = {"goal": "Add a new function x in the new file workspace/patching.py.",
               "target": {"path": "src/local_memory_lab/workspace/patching.py", "shape": "new_file", "symbol": ""}}
    return RoleReply(out, "", 5, 0.1, 50.0, 1.0, {"loadSeconds": 0.2})


def fake_plan(config, system, payload, schema, **kwargs):
    return RoleReply({"tasks": [{"title": "Stricter citations", "description": "Each part meets CITE_MIN.",
                                 "target": {"path": "src/local_memory_lab/team/steps.py",
                                            "symbol": "require_citation", "new": False}, "check": "tests"}]},
                     "", 5, 0.1, 50.0, 1.0)


class DraftProbeTests(unittest.TestCase):
    def test_dry_run_records_validity_planning_and_agreement(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary, \
                patch("local_memory_lab.team.draft.call_role", side_effect=fake_draft), \
                patch("local_memory_lab.team.plan.call_role", side_effect=fake_plan):
            output, summary = draft_probe.run_draft_probe(ROOT, on_progress=lambda message: None,
                                                          embedder=team_fixtures._NoModelEmbedder(),
                                                          probes_dir=Path(temporary))
            record = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(8, summary["drafts"])
        self.assertEqual((1, 1, 1, 1), (summary["valid"], summary["planned_validly"], summary["plan_target_agrees"],
                                        summary["draft_target_as_expected"]))
        self.assertIn("already exists", " ".join(summary["rejection_reasons"]))
        self.assertEqual(40, len(record["source_commit"]))
        self.assertEqual(["G1", "G2", "G3", "self-001", "self-006", "self-010", "self-020", "self-021"],
                         [row["case"] for row in record["rows"]])


if __name__ == "__main__":
    unittest.main()
