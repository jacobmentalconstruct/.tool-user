"""Durable session state is reconstructed from its event log."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.session import SharedSession  # noqa: E402


class SessionRestoreTests(unittest.TestCase):
    def test_session_domains_restore_from_events_after_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            store_path = root / "events.sqlite"

            first = SharedSession(store_path, load_models=False, start_worker=False)
            first.models = ["model-a"]
            first.set_project_root(str(project))
            first.select_model("model-a")
            first.add_note("keep this")
            first.store.append("user", "chat.prompt", {
                "display": {"speaker": "USER", "text": "hello"}, "requestId": "r-1",
            })
            first.store.append("system", "chat.reply", {
                "display": {"speaker": "Assistant", "text": "hi"},
                "requestId": "r-1", "turn": [
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "hi"},
                ],
            })

            restored = SharedSession(store_path, load_models=False, start_worker=False)

            self.assertEqual(project, restored.project_root)
            self.assertEqual("model-a", restored.model)
            self.assertEqual(["keep this"], restored.notes)
            self.assertEqual(1, len(restored.turns))
            self.assertEqual("hello", restored.events[0]["text"])
            self.assertEqual("hi", restored.events[1]["text"])


if __name__ == "__main__":
    unittest.main()
