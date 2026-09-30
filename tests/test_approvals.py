"""Non-blocking approvals, actor rights and cancellation."""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.session import SharedSession  # noqa: E402
from local_memory_lab.event_store import EventStore  # noqa: E402


class ApprovalTests(unittest.TestCase):
    def test_t2_patch_approval_events_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.sqlite"
            store = EventStore(path)
            store.append("system", "approval.requested", {
                "id": "old-approved", "kind": "patch", "title": "Apply project patch?",
                "name": "1 file(s), +1 / -1",
            })
            store.append("user", "approval.resolved", {"id": "old-approved", "approved": True})
            store.append("system", "approval.requested", {
                "id": "old-pending", "kind": "patch", "title": "Apply project patch?",
                "name": "2 file(s), +2 / -2",
            })
            restored = SharedSession(path, load_models=False, start_worker=False)
            self.assertEqual("approved", restored.state.approvals.records["old-approved"].state)
            self.assertEqual("expired", restored.state.approvals.records["old-pending"].state)
            self.assertEqual([], restored.state.approvals.pending())

    def test_only_user_resolves_and_cancels(self):
        with tempfile.TemporaryDirectory() as temp:
            session = SharedSession(Path(temp) / "events.sqlite", load_models=False,
                                    start_worker=False)
            session.transition_job("j-1", "queued", goal="repair")
            session.transition_job("j-1", "planning")
            session.transition_job("j-1", "awaiting_plan_approval")
            approval_id = session.request_approval(
                "plan", "Approve goal", "repair", actor="system", job="j-1")
            with self.assertRaisesRegex(ValueError, "Only the USER"):
                session.approve(approval_id, True, "AGENT")
            with self.assertRaisesRegex(ValueError, "Only the USER"):
                session.cancel_job("j-1", "AGENT")
            self.assertEqual("pending", session.state.approvals.records[approval_id].state)
            session.cancel_job("j-1", "USER")
            self.assertEqual("cancelled", session.state.jobs.records["j-1"].state)
            self.assertEqual("superseded", session.state.approvals.records[approval_id].state)
            with self.assertRaises(ValueError):
                session.approve(approval_id, True, "USER")

    def test_expired_or_rejected_approval_has_no_side_effect(self):
        with tempfile.TemporaryDirectory() as temp:
            session = SharedSession(Path(temp) / "events.sqlite", load_models=False,
                                    start_worker=False)
            approval_id = session.request_approval(
                "command", "Run tests", "tests", actor="role:builder")
            self.assertFalse(session.wait_for_approval(approval_id, timeout=0))
            self.assertEqual("expired", session.state.approvals.records[approval_id].state)
            approval_id = session.request_approval(
                "command", "Run tests", "tests", actor="role:builder")
            session.approve(approval_id, False)
            self.assertFalse(session.wait_for_approval(approval_id))
            self.assertEqual("rejected", session.state.approvals.records[approval_id].state)
            self.assertFalse(any(e["kind"] == "command.result" for e in session.events_after(0)))

    def test_pending_approval_does_not_block_chat(self):
        with tempfile.TemporaryDirectory() as temp:
            ready = threading.Event()
            holder: dict[str, SharedSession] = {}

            def fake_turn(prompt, model, turns, notes, tools, on_tool):
                if prompt == "blocked":
                    session = holder["session"]
                    approval_id = session.request_approval(
                        "patch", "Patch", "diff", actor="role:builder")
                    ready.set()
                    session.wait_for_approval(approval_id)
                return "reply to " + prompt, []

            with patch("local_memory_lab.session.run_turn", side_effect=fake_turn):
                session = SharedSession(Path(temp) / "events.sqlite", load_models=False,
                                        start_worker=True)
                holder["session"] = session
                session.submit("blocked", "USER")
                self.assertTrue(ready.wait(2))
                session.submit("free", "AGENT")
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    replies = [e for e in session.events_after(0) if e["kind"] == "chat.reply"]
                    if any(e["data"]["display"]["text"] == "reply to free" for e in replies):
                        break
                    time.sleep(0.01)
                else:
                    self.fail("Chat stayed blocked behind an approval.")
                pending_id = session.state.approvals.pending()[0].id
                session.approve(pending_id, True)
                deadline = time.monotonic() + 2
                while session.busy and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertFalse(session.busy)


if __name__ == "__main__":
    unittest.main()
