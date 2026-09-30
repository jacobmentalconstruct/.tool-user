"""Run one named project command from the USER-owned allowlist."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .workspace.paths import is_link


@dataclass(frozen=True)
class CommandSpec:
    name: str
    argv: tuple[str, ...]
    root: Path
    timeout_s: float
    max_output_bytes: int


class CommandRunner:
    def __init__(self, root: Path):
        self.root = Path(root).resolve(strict=True)

    def resolve(self, name: str) -> CommandSpec:
        if not isinstance(name, str) or not name:
            raise ValueError("Request an allowlisted command by name.")
        directory = self.root / ".lab"
        path = directory / "allowlist.json"
        if is_link(directory) or is_link(path):
            raise ValueError("The command allowlist cannot be a linked path.")
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError("The USER has not created .lab/allowlist.json.") from exc
        except json.JSONDecodeError as exc:
            raise ValueError("The command allowlist is invalid JSON.") from exc
        if not isinstance(config, dict) or not isinstance(config.get("commands"), dict):
            raise ValueError("The command allowlist needs a commands object.")
        argv = config["commands"].get(name)
        if not isinstance(argv, list) or not argv or any(
                not isinstance(arg, str) or not arg for arg in argv):
            raise ValueError("That command name is not allowlisted with a non-empty argv.")
        timeout = config.get("timeout_s", 300)
        output_limit = config.get("max_output_bytes", 20000)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= 3600:
            raise ValueError("timeout_s must be between 0 and 3600 seconds.")
        if not isinstance(output_limit, int) or isinstance(output_limit, bool) or not 32 <= output_limit <= 1_000_000:
            raise ValueError("max_output_bytes must be between 32 and 1,000,000.")
        return CommandSpec(name, tuple(argv), self.root, float(timeout), output_limit)

    @staticmethod
    def _stop_tree(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           capture_output=True, timeout=10, check=False)
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def run(self, spec: CommandSpec, cancelled: Callable[[], bool] | None = None) -> dict:
        if spec.root != self.root:
            raise ValueError("Command spec belongs to another project.")
        if cancelled and cancelled():
            return {"name": spec.name, "exit_code": -1, "duration_s": 0.0,
                    "output": "", "status": "cancelled"}
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        started = time.monotonic()
        process = subprocess.Popen(
            list(spec.argv), cwd=spec.root, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=False,
            creationflags=flags, start_new_session=sys.platform != "win32",
        )
        captured = bytearray()
        total_bytes = 0

        def drain():
            nonlocal total_bytes
            assert process.stdout is not None
            while chunk := os.read(process.stdout.fileno(), 4096):
                total_bytes += len(chunk)
                captured.extend(chunk)
                if len(captured) > spec.max_output_bytes:
                    del captured[:-spec.max_output_bytes]

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        status = ""
        while process.poll() is None:
            if cancelled and cancelled():
                status = "cancelled"
                self._stop_tree(process)
                break
            if time.monotonic() - started >= spec.timeout_s:
                status = "timeout"
                self._stop_tree(process)
                break
            time.sleep(0.02)
        if not status:
            status = "ok" if process.returncode == 0 else "failed"
        reader.join(timeout=5)
        if process.stdout:
            process.stdout.close()
        if total_bytes > spec.max_output_bytes:
            keep = spec.max_output_bytes
            while True:
                marker = f"[… {total_bytes - keep} bytes trimmed]\n".encode("utf-8")
                next_keep = max(0, spec.max_output_bytes - len(marker))
                if next_keep == keep:
                    break
                keep = next_keep
            output = marker + (captured[-keep:] if keep else b"")
        else:
            output = captured
        return {"name": spec.name, "exit_code": process.returncode if status in {"ok", "failed"} else -1,
                "duration_s": round(time.monotonic() - started, 3),
                "output": output.decode("utf-8", errors="ignore"), "status": status}
