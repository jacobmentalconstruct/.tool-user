"""Allowlisted execution and Windows process-tree cleanup. Since T6 commands run only as task checks."""

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
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.command_runner import CommandRunner  # noqa: E402
from local_memory_lab.interfaces.web import make_handler  # noqa: E402
from local_memory_lab.session import SharedSession  # noqa: E402
import team_fixtures  # noqa: E402,F401  (blocks every model call in the default suite)


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
