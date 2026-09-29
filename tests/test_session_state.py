"""Durable session state is reconstructed from its event log."""

from __future__ import annotations

import sys
import tempfile
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.agent.engine import DEFAULT_MODEL  # noqa: E402
from local_memory_lab.session import Approval, SharedSession  # noqa: E402
from local_memory_lab.interfaces.web import make_handler  # noqa: E402


class SessionRestoreTests(unittest.TestCase):
    def test_fresh_session_prefers_default_model_when_installed(self):
        with tempfile.TemporaryDirectory() as temp:
            session = SharedSession(Path(temp) / "events.sqlite", load_models=False, start_worker=False)
            session.models = ["phi3:mini-128k", DEFAULT_MODEL]
            self.assertEqual(DEFAULT_MODEL, session.model)

            session.models = ["phi3:mini-128k"]
            self.assertEqual("phi3:mini-128k", session.model)

    def test_http_state_and_event_cursor_read_from_the_log(self):
        with tempfile.TemporaryDirectory() as temp:
            session = SharedSession(Path(temp) / "events.sqlite", load_models=False, start_worker=False)
            session.add_note("persisted")
            session.submit("read through cursor", "AGENT")
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0), make_handler(session, "user-token", "agent-token"),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"

                def get(path):
                    request = Request(base + path, headers={"Authorization": "Bearer user-token"})
                    with urlopen(request, timeout=5) as response:
                        return json.load(response)

                state = get("/api/state")
                updates = get("/api/events?after=1")["events"]

                self.assertNotIn("events", state)
                self.assertEqual(2, state["lastEventId"])
                self.assertEqual(["persisted"], state["notes"])
                self.assertEqual([2], [event["id"] for event in updates])
                self.assertEqual("agent", updates[0]["actor"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

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
            first.add_note("remove this")
            first.add_note("keep this")
            first.remove_note(0)
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
            self.assertIn("hello", [event["text"] for event in restored.events])
            self.assertIn("hi", [event["text"] for event in restored.events])
            self.assertEqual({}, restored.state.conversation.pending_prompts)

    def test_restart_marks_an_unfinished_prompt_without_replaying_it(self):
        with tempfile.TemporaryDirectory() as temp:
            store_path = Path(temp) / "events.sqlite"
            first = SharedSession(store_path, load_models=False, start_worker=False)
            request_id = first.submit("unfinished", "USER")

            restored = SharedSession(store_path, load_models=False, start_worker=False)

            self.assertEqual(0, restored.prompts.qsize())
            self.assertEqual({}, restored.state.conversation.pending_prompts)
            event = restored.events[-1]
            self.assertEqual("Error", event["speaker"])
            self.assertIn("restarted", event["text"])
            self.assertEqual(request_id, restored.store.read_after(0)[-1]["data"]["requestId"])

    def test_rights_follow_actor_labels(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            session = SharedSession(root / "events.sqlite", load_models=False, start_worker=False)
            session.models = ["model-a"]

            session.add_note("agent may add notes", "AGENT")
            session.set_project_root(str(project), "USER")
            session.select_model("model-a", "USER")
            session.submit("agent may chat", "AGENT")
            queued_event = session.store.get(session.prompts.get_nowait())
            self.assertEqual("agent may chat", queued_event["data"]["display"]["text"])
            session.pending = Approval("approval-1", "request-1", "patch", "Patch", "file")

            with self.assertRaisesRegex(ValueError, "Only the USER"):
                session.set_project_root(str(project), "AGENT")
            with self.assertRaisesRegex(ValueError, "Only the USER"):
                session.select_model("model-a", "AGENT")
            with self.assertRaisesRegex(ValueError, "Only the USER"):
                session.approve("approval-1", True, "AGENT")
            session.approve("approval-1", True, "USER")
            with self.assertRaisesRegex(ValueError, "true or false"):
                session.approve("approval-1", 1, "USER")

            events = session.store.read_after(0)
            self.assertEqual("agent", next(e["actor"] for e in events if e["kind"] == "note.added"))
            self.assertEqual("agent", next(e["actor"] for e in events if e["kind"] == "chat.prompt"))
            self.assertEqual("user", next(e["actor"] for e in events if e["kind"] == "project.selected"))
            self.assertEqual("user", [e["actor"] for e in events if e["kind"] == "model.selected"][-1])
            self.assertEqual("user", next(e["actor"] for e in events if e["kind"] == "approval.resolved"))


if __name__ == "__main__":
    unittest.main()
