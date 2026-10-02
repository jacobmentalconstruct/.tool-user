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
from team_fixtures import make_project, pending_patch, planner, team_roles, wait_for  # noqa: E402


class FakeEmbedder:
    model = "test-embedder"

    def embed(self, text):
        return (1.0, 0.0)

    def embed_many(self, texts):
        return [(1.0, 0.0) for _ in texts]


class ApprovalTests(unittest.TestCase):
    def test_unapproved_prompts_run_in_order_with_prior_history(self):
        with tempfile.TemporaryDirectory() as temp:
            first_started = threading.Event()
            release_first = threading.Event()
            calls = []

            def fake_answer(prompt, model, turns, notes):
                calls.append((prompt, list(turns)))
                if prompt == "first":
                    first_started.set()
                    self.assertTrue(release_first.wait(2))
                return "reply to " + prompt, [{"role": "user", "content": prompt}]

            with patch("local_memory_lab.session.answer", side_effect=fake_answer):
                session = SharedSession(Path(temp) / "events.sqlite", load_models=False,
                                        start_worker=True)
                session.submit("first", "USER")
                self.assertTrue(first_started.wait(2))
                session.submit("second", "USER")
                self.assertEqual(["first"], [call[0] for call in calls])
                release_first.set()
                deadline = time.monotonic() + 2
                while len(calls) < 2 and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(["first", "second"], [call[0] for call in calls])
                self.assertEqual([[{"role": "user", "content": "first"}]], calls[1][1])
                while session.busy and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertFalse(session.busy)

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
            request = dict(actor="system", origin_role="role:builder", candidate="c-1")
            approval_id = session.request_approval("patch", "Patch", "diff", **request)
            self.assertFalse(session.wait_for_approval(approval_id, timeout=0))
            self.assertEqual("expired", session.state.approvals.records[approval_id].state)
            approval_id = session.request_approval("patch", "Patch", "diff", **request)
            session.approve(approval_id, False)
            self.assertFalse(session.wait_for_approval(approval_id))
            self.assertEqual("rejected", session.state.approvals.records[approval_id].state)
            self.assertFalse(any(e["kind"] in {"command.result", "tool.result"} for e in session.events_after(0)))

    def test_since_t6_only_the_lifecycle_requests_approvals(self):
        with tempfile.TemporaryDirectory() as temp:
            session = SharedSession(Path(temp) / "events.sqlite", load_models=False,
                                    start_worker=False)
            for kind, actor in (("patch", "role:builder"), ("command", "role:builder"), ("command", "system")):
                with self.subTest(kind=kind, actor=actor), self.assertRaises(ValueError):
                    session.request_approval(kind, "x", "y", actor=actor)

    def test_pending_approval_does_not_block_chat(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch("local_memory_lab.session.answer", return_value=("reply to free", [])):
                session = SharedSession(Path(temp) / "events.sqlite", load_models=False,
                                        start_worker=True)
                session.request_approval("plan", "Approve plan", "tasks", actor="system")
                session.submit("free", "AGENT")
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    replies = [e for e in session.events_after(0) if e["kind"] == "chat.reply"]
                    if any(e["data"]["display"]["text"] == "reply to free" for e in replies):
                        break
                    time.sleep(0.01)
                else:
                    self.fail("Chat stayed blocked behind an approval.")
                self.assertEqual(1, len(session.state.approvals.pending()))

    def test_running_goal_holds_turn_slot_until_it_parks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release_goal = threading.Event()
            chat_started = threading.Event()
            calls = []

            def fake_answer(prompt, model, turns, notes):
                chat_started.set()
                return "done", []

            with team_roles(hold=release_goal, calls=calls), \
                    patch("local_memory_lab.session.answer", side_effect=fake_answer), planner():
                session = SharedSession(root / "events.sqlite", load_models=False,
                                        start_worker=True, knowledge_embedder=FakeEmbedder())
                session.set_project_root(str(make_project(root / "project")))
                job_id = session.submit_goal("goal", "USER")
                wait_for(session, job_id, "awaiting_plan_approval")
                pending = session.state.approvals.pending()[0]
                session.approve(pending.id, True)
                deadline = time.monotonic() + 10
                while not calls and time.monotonic() < deadline:  # the goal's builder call is running
                    time.sleep(0.02)
                session.submit("chat", "USER")
                self.assertFalse(chat_started.wait(0.1))  # chat waits for the running role step
                release_goal.set()
                patch_approval = pending_patch(session, job_id)  # the goal parks at its patch approval...
                self.assertTrue(chat_started.wait(5))  # ...which frees the turn slot for chat
                session.approve(patch_approval.id, True)
                wait_for(session, job_id, "done", timeout=10)
                deadline = time.monotonic() + 2
                while session.busy and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertFalse(session.busy)


if __name__ == "__main__":
    unittest.main()
