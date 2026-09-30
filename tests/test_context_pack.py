"""Greedy context-pack shape, accounting, and session-path coverage."""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.knowledge.context_pack import assemble_context_pack  # noqa: E402
from local_memory_lab.session import SharedSession  # noqa: E402


class ContextPackTests(unittest.TestCase):
    def test_shape_greedy_selection_budget_and_dropped_count(self):
        candidates = [
            {"kind": "chunk", "path": "b.py", "lines": [1, 1], "score": 0.5, "text": "12345678"},
            {"kind": "summary", "path": "a.py", "lines": [1, 1], "score": 0.9, "text": "abcd"},
            {"kind": "file", "path": "d.py", "lines": [1, 1], "score": 0.2, "text": "efgh"},
            {"kind": "graph", "path": "c.py", "lines": [1, 1], "score": 0.1, "text": "x" * 20},
        ]
        pack = assemble_context_pack(candidates, budget_tokens=3)
        self.assertEqual({"budget_tokens", "used_tokens", "dropped", "items"}, set(pack))
        self.assertEqual(3, pack["budget_tokens"])
        self.assertEqual(3, pack["used_tokens"])
        self.assertEqual(2, pack["dropped"])
        self.assertEqual(["a.py", "b.py"], [item["path"] for item in pack["items"]])
        self.assertEqual({"kind", "path", "lines", "score", "text"}, set(pack["items"][0]))

    def test_shared_session_passes_retrieved_context_into_current_turn(self):
        with tempfile.TemporaryDirectory() as temp:
            session = SharedSession(Path(temp) / "events.sqlite", load_models=False)
            received = []

            def fake_turn(prompt, model, turns, notes, tools, on_tool, **kwargs):
                received.extend(notes)
                return "finished", [{"role": "user", "content": prompt}]

            with patch("local_memory_lab.session.KnowledgeService.context_for",
                       return_value="[guide.md:1-2]\nUseful context"), \
                 patch("local_memory_lab.session.run_turn", side_effect=fake_turn):
                session.submit("use the guide", "USER")
                deadline = time.monotonic() + 3
                while not any(event["kind"] == "chat.reply" for event in session.events_after(0)):
                    if time.monotonic() >= deadline:
                        self.fail("Session did not finish the test turn.")
                    time.sleep(0.01)
            self.assertIn("[guide.md:1-2]\nUseful context", received)


if __name__ == "__main__":
    unittest.main()
