"""HTTP adapter and browser server for the one shared session."""

from __future__ import annotations

import hmac
import json
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ..locations import CONTROL
from ..session import SharedSession


PAGE = Path(__file__).with_name("shared_ui.html")


def make_handler(session: SharedSession, user_token: str, agent_token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LocalMemoryLab/1.0"

        def log_message(self, format, *args):
            pass

        def _actor(self) -> str | None:
            header = self.headers.get("Authorization", "")
            token = header[7:] if header.startswith("Bearer ") else ""
            if hmac.compare_digest(token, user_token):
                return "USER"
            if hmac.compare_digest(token, agent_token):
                return "AGENT"
            return None

        def _json(self, status: int, data: dict):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _reject(self, status: int, message: str):
            self._json(status, {"error": message})

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path == "/":
                token = parse_qs(parsed.query).get("token", [""])[0]
                if not hmac.compare_digest(token, user_token):
                    self._reject(HTTPStatus.FORBIDDEN, "Open the private browser link for this session.")
                    return
                body = PAGE.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)
                return
            actor = self._actor()
            if actor is None:
                self._reject(HTTPStatus.UNAUTHORIZED, "Invalid session token.")
                return
            if parsed.path == "/api/state":
                self._json(HTTPStatus.OK, session.snapshot(actor))
            elif parsed.path == "/api/events":
                try:
                    raw_cursor = parse_qs(parsed.query).get("after", ["0"])[0]
                    cursor = int(raw_cursor)
                    self._json(HTTPStatus.OK, {"events": session.events_after(cursor)})
                except (ValueError, TypeError) as exc:
                    self._reject(HTTPStatus.BAD_REQUEST, str(exc))
            else:
                self._reject(HTTPStatus.NOT_FOUND, "Not found.")

        def do_POST(self):
            actor = self._actor()
            if actor is None:
                self._reject(HTTPStatus.UNAUTHORIZED, "Invalid session token.")
                return
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                self._reject(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Use JSON requests.")
                return
            origin = self.headers.get("Origin")
            expected = f"http://127.0.0.1:{self.server.server_port}"
            if origin is not None and origin != expected:
                self._reject(HTTPStatus.FORBIDDEN, "Cross-origin requests are not allowed.")
                return
            try:
                result = {"ok": True}
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 20_000:
                    raise ValueError("Request is empty or too large.")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("Request body must be an object.")
                if self.path == "/api/messages":
                    result["requestId"] = session.submit(payload.get("text"), actor)
                elif self.path == "/api/goals":
                    result["jobId"] = session.submit_goal(payload.get("text"), actor)
                elif self.path == "/api/goal-draft":  # fills only this browser's New goal box (D22)
                    if actor != "USER":
                        self._reject(HTTPStatus.FORBIDDEN, "Only the USER browser can draft a goal.")
                        return
                    draft = session.draft_goal(payload.get("source"), actor)
                    result.update({"valid": draft["valid"], "reasons": draft["reasons"]})
                    if draft["valid"]:
                        result["goal"] = draft["goal"]
                elif self.path == "/api/jobs/cancel":
                    if actor != "USER":
                        self._reject(HTTPStatus.FORBIDDEN, "Only the USER browser can cancel a job.")
                        return
                    session.cancel_job(payload.get("id"), actor)
                elif self.path == "/api/commands":
                    self._reject(HTTPStatus.FORBIDDEN, "Only a ROLE may request a named command.")
                    return
                elif self.path == "/api/notes":
                    session.add_note(payload.get("text"), actor)
                elif self.path == "/api/notes/remove":
                    session.remove_note(payload.get("index"), actor)
                elif self.path == "/api/model":
                    if actor != "USER":
                        self._reject(HTTPStatus.FORBIDDEN, "Only the USER browser can select a chat model.")
                        return
                    session.select_model(payload.get("name"), actor)
                elif self.path == "/api/project":
                    if actor != "USER":
                        self._reject(HTTPStatus.FORBIDDEN, "Only the USER browser can select a project folder.")
                        return
                    session.set_project_root(payload.get("path"), actor)
                elif self.path == "/api/approval":
                    if actor != "USER":
                        self._reject(HTTPStatus.FORBIDDEN, "Only the USER browser can resolve approvals.")
                        return
                    decision = payload.get("approved")
                    if not isinstance(decision, bool):
                        raise ValueError("approved must be true or false.")
                    session.approve(payload.get("id"), decision, actor)
                else:
                    self._reject(HTTPStatus.NOT_FOUND, "Not found.")
                    return
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self._reject(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._json(HTTPStatus.OK, result)

    return Handler


def main():
    user_token = secrets.token_urlsafe(24)
    agent_token = secrets.token_urlsafe(24)
    session = SharedSession()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(session, user_token, agent_token))
    host = f"http://127.0.0.1:{server.server_port}"
    CONTROL.mkdir(exist_ok=True)
    control_path = CONTROL / "shared.json"
    control_path.write_text(json.dumps({
        "browser_url": f"{host}/?token={user_token}",
        "api_url": host,
        "agent_token": agent_token,
    }, indent=2), encoding="utf-8")
    print(f"Shared session ready at {host} (browser link in {control_path})", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        control_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
