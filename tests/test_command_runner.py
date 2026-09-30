"""Allowlisted execution and Windows process-tree cleanup."""

from __future__ import annotations

import ctypes
import json
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.agent.tool_router import SharedTools  # noqa: E402
from local_memory_lab.command_runner import CommandRunner  # noqa: E402
from local_memory_lab.interfaces.web import make_handler  # noqa: E402
from local_memory_lab.session import SharedSession  # noqa: E402


class CommandRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "project"
        (self.root / ".lab").mkdir(parents=True)

    def tearDown(self):
        self.temporary.cleanup()

    def allow(self, commands, *, timeout=3, limit=20000):
        (self.root / ".lab" / "allowlist.json").write_text(json.dumps({
            "commands": commands, "timeout_s": timeout, "max_output_bytes": limit,
        }), encoding="utf-8")

    def test_exact_argv_and_project_cwd(self):
        self.allow({"where": [sys.executable, "-c", "import os;print(os.getcwd());print('x'*100)"]},
                   limit=20000)
        runner = CommandRunner(self.root)
        result = runner.run(runner.resolve("where"))
        self.assertEqual("ok", result["status"])
        self.assertEqual(0, result["exit_code"])
        self.assertTrue(result["output"].startswith(str(self.root)))
        self.assertIn("x" * 100, result["output"])

    def test_output_cap_preserves_final_status_line(self):
        self.allow({"tests": [sys.executable, "-c",
                              "print('x'*500);print('FAILED (failures=1)')"]}, limit=120)
        runner = CommandRunner(self.root)
        result = runner.run(runner.resolve("tests"))
        self.assertTrue(result["output"].startswith("[… "))
        self.assertIn("bytes trimmed]\n", result["output"])
        self.assertEqual("FAILED (failures=1)", result["output"].splitlines()[-1])
        self.assertLessEqual(len(result["output"].encode("utf-8")), 120)

    def test_name_only_lookup_refuses_unknown_and_invalid_allowlist(self):
        self.allow({"tests": [sys.executable, "-c", "print('ok')"]})
        runner = CommandRunner(self.root)
        for name in ("tests extra", "", ["tests"]):
            with self.subTest(name=name), self.assertRaises(ValueError):
                runner.resolve(name)
        self.allow({"bad": "python -c print(1)"})
        with self.assertRaises(ValueError):
            runner.resolve("bad")
        schema = next(item for item in SharedTools.schemas
                      if item["function"]["name"] == "run_command")
        self.assertEqual(["name"], schema["function"]["parameters"]["required"])
        self.assertEqual(["name"], list(schema["function"]["parameters"]["properties"]))

    def test_command_waits_for_user_approval_and_logs_result(self):
        marker = self.root / "ran.txt"
        self.allow({"write": [sys.executable, "-c",
                              "from pathlib import Path;Path('ran.txt').write_text('yes')"]})
        session = SharedSession(self.root / "events.sqlite", load_models=False, start_worker=False)
        results = []
        worker = threading.Thread(target=lambda: results.append(
            session._run_named_command("write", self.root, "request-1")))
        worker.start()
        deadline = time.monotonic() + 2
        while not session.state.approvals.pending() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(marker.exists())
        pending = session.state.approvals.pending()[0]
        self.assertEqual("command", pending.kind)
        self.assertIn("ran.txt", pending.detail)
        with self.assertRaisesRegex(ValueError, "Only the USER"):
            session.approve(pending.id, True, "AGENT")
        session.approve(pending.id, True, "USER")
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual("yes", marker.read_text(encoding="utf-8"))
        self.assertEqual("ok", results[0]["status"])
        events = [event for event in session.events_after(0) if event["kind"] == "command.result"]
        self.assertEqual(1, len(events))
        self.assertEqual(("write", "ok", 0),
                         (events[0]["data"]["name"], events[0]["data"]["status"],
                          events[0]["data"]["exit_code"]))

    def test_direct_browser_and_agent_command_requests_are_refused(self):
        session = SharedSession(self.root / "events.sqlite", load_models=False, start_worker=False)
        with self.assertRaisesRegex(ValueError, "Only the lifecycle or a ROLE"):
            session.request_approval("command", "Run tests", "tests", actor="agent")
        server = ThreadingHTTPServer(("127.0.0.1", 0),
                                     make_handler(session, "user-token", "agent-token"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for token in ("user-token", "agent-token"):
                with self.subTest(token=token):
                    request = Request(f"http://127.0.0.1:{server.server_port}/api/commands",
                                      data=b'{"name":"tests"}', headers={
                                          "Authorization": "Bearer " + token,
                                          "Content-Type": "application/json",
                                      })
                    with self.assertRaises(HTTPError) as error:
                        urlopen(request, timeout=5)
                    self.assertEqual(403, error.exception.code)
            self.assertEqual([], [event for event in session.events_after(0)
                                  if event["kind"] == "command.result"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_chat_loop_requests_command_as_role(self):
        marker = self.root / "ran.txt"
        self.allow({"write": [sys.executable, "-c",
                              "from pathlib import Path;Path('ran.txt').write_text('yes')"]})
        calls = 0

        def fake_ollama(path, payload):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"message": {"role": "assistant", "tool_calls": [{"function": {
                    "name": "run_command", "arguments": {"name": "write"},
                }}]}}
            return {"message": {"role": "assistant", "content": "done"}}

        with patch("local_memory_lab.agent.engine.ollama_json", side_effect=fake_ollama):
            session = SharedSession(self.root / "events.sqlite", load_models=False,
                                    start_worker=True)
            session.set_project_root(str(self.root))
            session.submit("run the named check", "AGENT")
            deadline = time.monotonic() + 2
            while not session.state.approvals.pending() and time.monotonic() < deadline:
                time.sleep(0.01)
            pending = session.state.approvals.pending()[0]
            requested = [e for e in session.events_after(0) if e["kind"] == "approval.requested"]
            self.assertEqual("role:builder", requested[-1]["actor"])
            self.assertFalse(marker.exists())
            session.approve(pending.id, True, "USER")
            deadline = time.monotonic() + 3
            while session.busy and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(session.busy)
            self.assertEqual("yes", marker.read_text(encoding="utf-8"))
            self.assertTrue(any(e["kind"] == "command.result" for e in session.events_after(0)))

    def test_unlisted_chat_command_is_refused_and_logged(self):
        self.allow({"write": [sys.executable, "-c", "print('ok')"]})
        calls = 0

        def fake_ollama(path, payload):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"message": {"role": "assistant", "tool_calls": [{"function": {
                    "name": "run_command", "arguments": {"name": "unknown"},
                }}]}}
            return {"message": {"role": "assistant", "content": "refused"}}

        with patch("local_memory_lab.agent.engine.ollama_json", side_effect=fake_ollama):
            session = SharedSession(self.root / "events.sqlite", load_models=False,
                                    start_worker=True)
            session.set_project_root(str(self.root))
            session.submit("try unknown", "AGENT")
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                events = session.events_after(0)
                if any(e["kind"] == "chat.reply" for e in events):
                    break
                time.sleep(0.01)
            else:
                self.fail("Chat did not complete after the command refusal.")
            refused = [e for e in events if e["kind"] == "tool.result"]
            self.assertEqual("error", refused[-1]["data"]["toolStatus"])
            self.assertFalse(any(e["kind"] == "command.result" for e in events))
            while session.busy and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(session.busy)

    @unittest.skipUnless(sys.platform == "win32", "Windows process-tree contract")
    def test_timeout_and_cancel_remove_child_processes(self):
        child_code = "import time;time.sleep(30)"
        parent_code = ("import subprocess,sys,time,pathlib;"
                       "p=subprocess.Popen([sys.executable,'-c',sys.argv[2]]);"
                       "pathlib.Path(sys.argv[1]).write_text(str(p.pid));time.sleep(30)")
        pid_file = self.root / "child.pid"
        self.allow({"tree": [sys.executable, "-c", parent_code, str(pid_file), child_code]},
                   timeout=1)
        runner = CommandRunner(self.root)

        def child_alive(pid):
            kernel = ctypes.windll.kernel32
            handle = kernel.OpenProcess(0x00100000, False, pid)
            if not handle:
                return False
            try:
                return kernel.WaitForSingleObject(handle, 0) == 0x102
            finally:
                kernel.CloseHandle(handle)

        def assert_child_gone():
            pid = int(pid_file.read_text(encoding="utf-8"))
            deadline = time.monotonic() + 2
            while child_alive(pid) and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertFalse(child_alive(pid), f"Child process {pid} survived.")

        timed_out = runner.run(runner.resolve("tree"))
        self.assertEqual("timeout", timed_out["status"])
        assert_child_gone()

        pid_file.unlink()
        cancelled = threading.Event()
        results = []
        worker = threading.Thread(target=lambda: results.append(
            runner.run(runner.resolve("tree"), cancelled=cancelled.is_set)))
        worker.start()
        deadline = time.monotonic() + 2
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(pid_file.exists(), "Parent did not create the child process.")
        cancelled.set()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual("cancelled", results[0]["status"])
        assert_child_gone()


if __name__ == "__main__":
    unittest.main()
