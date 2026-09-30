"""The browser and CLI expose a separate New goal lifecycle."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.interfaces import client  # noqa: E402
from local_memory_lab.interfaces.web import make_handler  # noqa: E402
from local_memory_lab.session import SharedSession  # noqa: E402


class JobInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        project = root / "project"
        project.mkdir()
        self.session = SharedSession(root / "events.sqlite", load_models=False, start_worker=False)
        self.session.set_project_root(str(project))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0),
                                          make_handler(self.session, "user-token", "agent-token"))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def request(self, path, body=None, token="user-token"):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(self.base + path, data=data, headers={
            "Authorization": "Bearer " + token,
            **({"Content-Type": "application/json"} if data else {}),
        })
        with urlopen(request, timeout=5) as response:
            return json.load(response)

    def wait_for(self, job_id, state):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            jobs = self.request("/api/state")["jobs"]
            if next(job for job in jobs if job["id"] == job_id)["state"] == state:
                return
            time.sleep(0.01)
        self.fail(f"Job {job_id} did not reach {state}.")

    def test_goal_from_agent_waits_for_user_plan_approval_then_runs(self):
        with patch("local_memory_lab.session.run_turn", return_value=("finished", [])) as run:
            job_id = self.request("/api/goals", {"text": "repair project"},
                                  token="agent-token")["jobId"]
            state = self.request("/api/state")
            self.assertEqual("awaiting_plan_approval", state["jobs"][0]["state"])
            self.assertEqual("plan", state["pendingApproval"]["kind"])
            self.assertNotIn("detail", self.request("/api/state", token="agent-token")["pendingApproval"])
            self.request("/api/approval", {"id": state["pendingApproval"]["id"], "approved": True})
            self.wait_for(job_id, "done")
            self.assertEqual("repair project", run.call_args.args[0])
            events = [e for e in self.session.events_after(0) if e.get("job") == job_id
                      and e["kind"] == "job.state"]
            self.assertEqual(["queued", "planning", "awaiting_plan_approval", "running", "done"],
                             [e["data"]["state"] for e in events])
            self.assertEqual("agent", events[0]["data"]["submittedBy"])
            self.assertTrue(all(e["actor"] == "system" for e in events))

    def test_user_rejection_and_user_only_cancellation(self):
        job_id = self.request("/api/goals", {"text": "first"})["jobId"]
        approval_id = self.request("/api/state")["pendingApproval"]["id"]
        self.request("/api/approval", {"id": approval_id, "approved": False})
        self.wait_for(job_id, "rejected")

        job_id = self.request("/api/goals", {"text": "second"})["jobId"]
        with self.assertRaises(HTTPError) as error:
            self.request("/api/jobs/cancel", {"id": job_id}, token="agent-token")
        self.assertEqual(403, error.exception.code)
        self.request("/api/jobs/cancel", {"id": job_id})
        self.wait_for(job_id, "cancelled")

    def test_expired_plan_approval_fails_without_running(self):
        with patch("local_memory_lab.session.run_turn") as run:
            job_id = self.request("/api/goals", {"text": "waited goal"})["jobId"]
            approval_id = self.request("/api/state")["pendingApproval"]["id"]
            self.session._resolve_approval(approval_id, "expired", "system")
            self.wait_for(job_id, "failed")
            self.assertEqual("plan approval expired", self.session.state.jobs.records[job_id].reason)
            run.assert_not_called()

    def test_user_can_cancel_a_running_goal_without_done_event(self):
        entered = threading.Event()
        release = threading.Event()

        def fake_turn(*args, **kwargs):
            entered.set()
            release.wait(2)
            if kwargs["cancelled"]():
                raise RuntimeError("Job cancelled.")
            return "finished", []

        with patch("local_memory_lab.session.run_turn", side_effect=fake_turn):
            job_id = self.request("/api/goals", {"text": "running goal"})["jobId"]
            approval_id = self.request("/api/state")["pendingApproval"]["id"]
            self.request("/api/approval", {"id": approval_id, "approved": True})
            self.assertTrue(entered.wait(2))
            self.request("/api/jobs/cancel", {"id": job_id})
            release.set()
            self.wait_for(job_id, "cancelled")
            deadline = time.monotonic() + 2
            while self.session.busy and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(self.session.busy)
            states = [e["data"]["state"] for e in self.session.events_after(0)
                      if e["kind"] == "job.state" and e.get("job") == job_id]
            self.assertNotIn("done", states)

    def test_browser_and_cli_show_goal_entrance_and_stage(self):
        job_id = self.request("/api/goals", {"text": "visible goal"})["jobId"]
        with urlopen(self.base + "/?token=user-token", timeout=5) as response:
            page = response.read().decode("utf-8")
        self.assertIn('id="goalForm"', page)
        self.assertIn('id="jobs"', page)
        config = {"api_url": self.base, "agent_token": "agent-token"}
        output = io.StringIO()
        with patch.object(client, "connect", return_value=config), redirect_stdout(output):
            client.main(["status"])
        self.assertIn("awaiting_plan_approval", output.getvalue())
        self.request("/api/jobs/cancel", {"id": job_id})
        self.wait_for(job_id, "cancelled")


if __name__ == "__main__":
    unittest.main()
