"""HTTP smoke coverage for project selection, approval resolution and event reads."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.interfaces.web import make_handler  # noqa: E402
from local_memory_lab.workspace.paths import choose_root  # noqa: E402


class SessionStub:
    def __init__(self):
        self.project_root = None
        self.pending = None
        self.log_events = []

    def set_project_root(self, path, actor):
        self.project_root = choose_root(path)

    def snapshot(self, actor):
        approval = None
        if self.pending:
            approval = {"id": self.pending["id"], "kind": "patch", "title": "Apply project patch?",
                        "name": self.pending["name"], "diff": self.pending["diff"]}
        return {"projectRoot": str(self.project_root) if self.project_root else None,
                "pendingApproval": approval, "lastEventId": max((e["id"] for e in self.log_events), default=0),
                "busy": False, "queueLength": 0,
                "model": "test", "models": [], "modelError": "", "notes": []}

    def events_after(self, cursor):
        return [event for event in self.log_events if event["id"] > cursor]

    def approve(self, approval_id, approved, actor):
        if not self.pending or self.pending["id"] != approval_id:
            raise ValueError("This approval is no longer pending.")
        self.pending["approved"] = approved


class HttpSmokeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.project = self.base / "project"
        self.control = self.base / "control"
        self.project.mkdir()
        self.control.mkdir()
        self.target = self.project / "sample.txt"
        self.target.write_text("before\n", encoding="utf-8")
        self.session = SessionStub()
        self.user_token = "user-test-token"
        self.agent_token = "agent-test-token"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.session, self.user_token, self.agent_token))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.api = f"http://127.0.0.1:{self.server.server_port}"
        self.assertIsNone(self.request("/api/state")["projectRoot"])
        self.select_project()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def request(self, path, body=None, token=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = Request(self.api + path, data=data, headers={
            "Authorization": "Bearer " + (token or self.user_token),
            **({"Content-Type": "application/json"} if data else {}),
        })
        with urlopen(req, timeout=5) as response:
            return json.load(response)

    def select_project(self):
        self.request("/api/project", {"path": str(self.project)})
        self.assertEqual(Path(self.request("/api/state")["projectRoot"]), self.project)

    def test_browser_sees_the_pending_patch_and_resolves_it_over_http(self):
        for decision in (False, True):
            with self.subTest(decision=decision):
                self.session.pending = {"id": str(uuid4()), "name": "Task 1: Fix", "diff": "+after"}
                pending = self.request("/api/state")["pendingApproval"]
                self.assertEqual("+after", pending["diff"])
                self.request("/api/approval", {"id": pending["id"], "approved": decision})
                self.assertIs(decision, self.session.pending["approved"])
        self.assertEqual("before\n", self.target.read_text(encoding="utf-8"))  # applying is the job runner's

    def test_agent_cannot_select_model_project_or_approval(self):
        from urllib.error import HTTPError

        for path, body in (
            ("/api/model", {"name": "model-a"}),
            ("/api/project", {"path": str(self.project)}),
            ("/api/approval", {"id": "approval-1", "approved": True}),
        ):
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                self.request(path, body, token=self.agent_token)
            self.assertEqual(403, error.exception.code)

    def test_event_endpoint_reads_after_cursor(self):
        first = {"id": 1, "actor": "user", "kind": "chat.prompt", "data": {}}
        second = {"id": 2, "actor": "system", "kind": "chat.reply", "data": {}}
        self.session.log_events = [first, second]

        self.assertEqual([second], self.request("/api/events?after=1")["events"])


if __name__ == "__main__":
    unittest.main()
