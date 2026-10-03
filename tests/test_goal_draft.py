"""Goal drafts (T8, D22): code validates every draft before it may fill the New goal box (no model calls)."""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.interfaces.web import make_handler  # noqa: E402
from local_memory_lab.session import SharedSession  # noqa: E402
from local_memory_lab.team.draft import draft_text, validate_draft  # noqa: E402
from local_memory_lab.workspace.paths import Workspace  # noqa: E402
import team_fixtures  # noqa: E402,F401  (blocks every model call in the default suite)
from team_fixtures import make_project, planner  # noqa: E402

FILES = {"team/steps.py": "def require_citation(card):\n    return card\n",
         "pkg/box.py": "class Box:\n    def put(self, item):\n        return item\n\n\ndef helper():\n    return 1\n",
         "pkg/util.py": "def tidy():\n    return 0\n",
         "other/util.py": "def tidy():\n    return 1\n",
         ".lab/allowlist.json": "{}\n"}


def draft(goal, path, shape, symbol):
    return {"goal": goal, "target": {"path": path, "shape": shape, "symbol": symbol}}


class DraftValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        for relative, text in FILES.items():
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text(text, encoding="utf-8")
        self.project = Workspace(root)

    def tearDown(self):
        self.temporary.cleanup()

    def reasons(self, *args):
        return validate_draft(draft(*args), self.project)

    def test_each_shape_is_accepted_when_its_target_and_wording_fit(self):
        for args in (("In team/steps.py, make require_citation reject short quote parts.",
                      "team/steps.py", "existing_symbol", "require_citation"),
                     ("Add a new function stale_temps to the existing file pkg/box.py.",
                      "pkg/box.py", "new_function", "stale_temps"),
                     ("Add a new method take to class Box in pkg/box.py.", "pkg/box.py", "new_method", "Box.take"),
                     ("Create the new file pkg/extra.py with one constant.", "pkg/extra.py", "new_file", "")):
            with self.subTest(shape=args[2]):
                self.assertEqual([], self.reasons(*args))

    def test_a_new_function_declared_as_a_new_file_is_rejected(self):  # the G3 confusion
        reasons = self.reasons("Add a new function stale_temps in the new file pkg/box.py.", "pkg/box.py",
                               "new_file", "")
        self.assertIn("already exists", " ".join(reasons))

    def test_the_shape_must_be_stated_in_words(self):
        reasons = self.reasons("Add stale_temps to pkg/box.py.", "pkg/box.py", "new_function", "stale_temps")
        self.assertIn("new function", " ".join(reasons))

    def test_a_symbol_that_does_not_fit_the_shape_is_rejected(self):
        for args in (("Add a new function x to the existing file pkg/box.py.", "pkg/box.py", "new_function", "Box.x"),
                     ("Add a new method take to class Box in pkg/box.py.", "pkg/box.py", "new_method", "take"),
                     ("Edit helper in pkg/box.py.", "pkg/box.py", "existing_symbol", "")):
            with self.subTest(args=args):
                self.assertTrue(self.reasons(*args))

    def test_unsafe_paths_and_the_allowlist_file_are_rejected(self):
        for path in (".lab/allowlist.json", "../outside.py", "/abs.py", "pkg\\box.py"):
            with self.subTest(path=path):
                self.assertTrue(self.reasons(f"Edit helper in {path}.", path, "existing_symbol", "helper"))

    def test_the_goal_names_its_file_unambiguously_and_no_other(self):
        self.assertEqual([], self.reasons("Make require_citation stricter in steps.py.", "team/steps.py",
                                          "existing_symbol", "require_citation"))  # a unique suffix
        self.assertIn("more than one", " ".join(self.reasons("Make tidy return 2 in util.py.", "pkg/util.py",
                                                             "existing_symbol", "tidy")))
        self.assertIn("another project file", " ".join(self.reasons(
            "Make helper in pkg/box.py match pkg/util.py.", "pkg/box.py", "existing_symbol", "helper")))
        self.assertIn("must name its file", " ".join(self.reasons("Make helper return 2.", "pkg/box.py",
                                                                  "existing_symbol", "helper")))

    def test_the_goal_is_one_bounded_line(self):
        for goal in ("Edit helper in pkg/box.py.\nThen more.", "Edit helper in pkg/box.py. " + "x" * 400, " "):
            with self.subTest(goal=goal[:20]):
                self.assertIn("one line", " ".join(self.reasons(goal, "pkg/box.py", "existing_symbol", "helper")))

    def test_the_chat_text_always_carries_the_check_first_note(self):
        for record in ({"valid": True, "goal": "g", "reasons": [], "checks": ["tests"]},
                       {"valid": False, "goal": "", "reasons": ["bad path"], "checks": []}):
            with self.subTest(valid=record["valid"]):
                self.assertIn("a check that fails before this change must exist", draft_text(record))


GOOD = {"goal": "In calc.py, make add return the sum of a and b.",
        "target": {"path": "calc.py", "shape": "existing_symbol", "symbol": "add"}}
BAD = {"goal": "Add a new function add in the new file calc.py.",  # the G3 confusion
       "target": {"path": "calc.py", "shape": "new_file", "symbol": ""}}


class DraftSessionTests(unittest.TestCase):
    """The session and endpoint: a draft fills only the requesting browser and never creates a job."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self.temporary.name)
        self.session = SharedSession(base / "events.sqlite", load_models=False, start_worker=False)
        self.session.set_project_root(str(make_project(base / "project")))
        self.session._record("system", "chat.reply", {"display": {"speaker": "Assistant", "text": "add is wrong"},
                                                      "turn": [{"role": "user", "content": "why does add fail?"},
                                                               {"role": "assistant", "content": "add is wrong"}]})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.session, "user-t", "agent-t"))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def tearDown(self):
        self.session.knowledge.close()
        self.temporary.cleanup()

    def request(self, path, body=None, token="user-t"):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(f"http://127.0.0.1:{self.server.server_port}{path}", data=data, headers={
            "Authorization": "Bearer " + token, **({"Content-Type": "application/json"} if data else {})})
        with urlopen(request, timeout=10) as response:
            return json.load(response)

    def draft(self, reply, source="conversation", token="user-t"):
        with planner(reply, target="local_memory_lab.team.draft.call_role") as call:
            return self.request("/api/goal-draft", {"source": source}, token), call

    def kinds(self):
        return [event["kind"] for event in self.session.events_after(0)]

    def test_a_valid_draft_returns_exactly_the_recorded_goal_and_creates_no_job_or_turn(self):
        turns = len(self.session.turns)
        response, call = self.draft(GOOD)
        recorded = self.session.events_after(0)[-1]["data"]
        self.assertEqual((True, GOOD["goal"]), (response["valid"], response["goal"]))
        self.assertEqual(response["goal"], recorded["goalDraft"]["goal"])
        self.assertNotIn("turn", recorded)
        self.assertEqual(turns, len(self.session.turns))
        self.assertFalse({"job.state", "approval.requested"} & set(self.kinds()))
        self.assertIn("conversation", call.call_args.args[2])  # what the draft model saw
        self.assertEqual(["tests"], list(call.call_args.args[2]["checks"]))

    def test_a_failed_validation_returns_no_goal(self):
        response, _call = self.draft(BAD)
        self.assertFalse(response["valid"])
        self.assertNotIn("goal", response)
        self.assertIn("already exists", " ".join(response["reasons"]))
        self.assertFalse(self.session.events_after(0)[-1]["data"]["goalDraft"]["valid"])

    def test_one_assistant_reply_can_be_the_source(self):
        reply_id = self.session.events_after(0)[-1]["id"]
        response, call = self.draft(GOOD, source=reply_id)
        self.assertTrue(response["valid"])
        self.assertEqual("add is wrong", call.call_args.args[2]["conversation"])

    def test_the_agent_client_is_refused(self):
        with self.assertRaises(HTTPError) as error:
            self.draft(GOOD, token="agent-t")
        self.assertEqual(403, error.exception.code)
        self.assertNotIn("goalDraft", json.dumps(self.session.events_after(0)))

    def test_a_held_turn_slot_answers_busy_at_once_and_fills_nothing(self):
        self.session._turn_slot.acquire()
        try:
            started = time.monotonic()
            response, call = self.draft(GOOD)
        finally:
            self.session._turn_slot.release()
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual((False, False), (response["valid"], call.called))
        self.assertNotIn("goal", response)
        self.assertIn("busy", self.session.events_after(0)[-1]["data"]["goalDraft"]["reasons"][0])

    def test_the_fill_reaches_only_the_requesting_browser(self):
        response, _call = self.draft(GOOD)
        self.assertEqual(GOOD["goal"], response["goal"])
        for token in ("user-t", "agent-t"):  # shared state carries the record, never a fill instruction
            with self.subTest(token=token):
                self.assertNotIn("goal-draft", json.dumps(self.request("/api/state", token=token)).casefold())


if __name__ == "__main__":
    unittest.main()
