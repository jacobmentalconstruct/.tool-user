"""T3 job transitions, restart recovery and the future task table."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.lifecycles import Jobs, validate_task_transition  # noqa: E402
from local_memory_lab.session import SharedSession  # noqa: E402


class LifecycleTests(unittest.TestCase):
    def test_job_path_and_invalid_transitions(self):
        jobs = Jobs()
        with self.assertRaises(ValueError):
            jobs.transition_data("j-1", "running")
        for state in ("queued", "planning", "awaiting_plan_approval", "running", "done"):
            data = jobs.transition_data("j-1", state, goal="repair" if state == "queued" else "")
            jobs.apply({"kind": "job.state", "job": "j-1", "data": data})
        self.assertEqual("done", jobs.records["j-1"].state)
        with self.assertRaises(ValueError):
            jobs.transition_data("j-1", "running")

    def test_rejected_and_cancelled_jobs_are_terminal(self):
        for terminal in ("rejected", "cancelled"):
            with self.subTest(terminal=terminal):
                jobs = Jobs()
                for state in ("queued", "planning", "awaiting_plan_approval", terminal):
                    jobs.apply({"kind": "job.state", "job": "j-1",
                                "data": jobs.transition_data("j-1", state, goal="repair")})
                self.assertEqual([], jobs.active_ids())

    def test_restart_marks_unfinished_jobs_failed_without_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.sqlite"
            session = SharedSession(path, load_models=False, start_worker=False)
            session.transition_job("j-1", "queued", goal="repair")
            session.transition_job("j-1", "planning")
            session.transition_job("j-1", "awaiting_plan_approval")
            approval_id = session.request_approval(
                "plan", "Approve goal", "repair", actor="system", job="j-1")
            restarted = SharedSession(path, load_models=False, start_worker=False)
            job = restarted.state.jobs.records["j-1"]
            self.assertEqual(("failed", "interrupted by restart"), (job.state, job.reason))
            self.assertEqual("expired", restarted.state.approvals.records[approval_id].state)
            self.assertEqual(0, restarted.prompts.qsize())
            self.assertEqual("job.state", restarted.events_after(0)[-1]["kind"])
            self.assertEqual("system", restarted.events_after(0)[-1]["actor"])

    def test_task_table_limits_debugging_to_two_rounds(self):
        for source, target in ((None, "pending"), ("pending", "building"),
                               ("building", "testing"), ("testing", "debugging"),
                               ("debugging", "testing"), ("testing", "reviewing"),
                               ("reviewing", "gated"), ("gated", "awaiting_approval"),
                               ("awaiting_approval", "applied")):
            validate_task_transition(source, target)
        with self.assertRaisesRegex(ValueError, "two debugging rounds"):
            validate_task_transition("testing", "debugging", debug_rounds=2)
        with self.assertRaises(ValueError):
            validate_task_transition("applied", "building")


if __name__ == "__main__":
    unittest.main()
