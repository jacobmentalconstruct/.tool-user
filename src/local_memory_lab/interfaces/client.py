"""Small command-line client for the agent side of a shared session."""

from __future__ import annotations

import argparse
import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


from ..locations import CONTROL as CONTROL_DIR


CONTROL = CONTROL_DIR / "shared.json"


def connect():
    try:
        return json.loads(CONTROL.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError("Shared session is not running. Start it with: python lab.py hub-server") from exc


def api(config: dict, path: str, data: dict | None = None):
    body = None if data is None else json.dumps(data).encode("utf-8")
    request = Request(config["api_url"] + path, data=body,
                      headers={"Authorization": "Bearer " + config["agent_token"],
                               "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=15) as response:
            return json.load(response)
    except HTTPError as exc:
        try:
            detail = json.load(exc).get("error", str(exc))
        except ValueError:
            detail = str(exc)
        raise RuntimeError(detail) from exc
    except URLError as exc:
        raise RuntimeError("Shared session is not reachable.") from exc


def show_events(events: list[dict], after_id: int = 0, request_id: str | None = None):
    for event in events:
        if event["id"] <= after_id:
            continue
        if request_id is not None and event.get("requestId") not in (None, request_id):
            continue
        print(f"[{event['speaker']}] {event['text']}", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Talk to the one running Local Memory Lab session")
    sub = parser.add_subparsers(dest="command", required=True)
    send = sub.add_parser("send", help="Queue a prompt")
    send.add_argument("text")
    send.add_argument("--wait", action="store_true", help="Watch until the reply arrives")
    send.add_argument("--timeout", type=int, default=180)
    sub.add_parser("status", help="Show current session status")
    sub.add_parser("watch", help="Watch new conversation events")
    remember = sub.add_parser("remember", help="Add a session note")
    remember.add_argument("text")
    args = parser.parse_args(argv)
    config = connect()

    if args.command == "status":
        state = api(config, "/api/state")
        print(f"Model: {state['model']} | Busy: {state['busy']} | Queued: {state['queueLength']}")
        if state["pendingApproval"]:
            print("Waiting for human approval:", state["pendingApproval"]["name"])
        print(f"Conversation events: {len(state['events'])} | Notes: {len(state['notes'])}")
    elif args.command == "remember":
        api(config, "/api/notes", {"text": args.text})
        print("Note added to the shared session.")
    elif args.command == "send":
        before = api(config, "/api/state")
        cursor = max((event["id"] for event in before["events"]), default=0)
        response = api(config, "/api/messages", {"text": args.text})
        request_id = response.get("requestId")
        print("Prompt queued.", flush=True)
        if args.wait:
            legacy_position = None
            if request_id is None:
                # A hub started before request IDs were added still has one
                # serialized queue. Count the outstanding prompts up to ours.
                observed = api(config, "/api/state")["events"]
                own = [event for event in observed if event["id"] > cursor and
                       event["speaker"] == "Codex" and event["text"] == args.text]
                if not own:
                    raise RuntimeError("Could not locate the queued prompt in the shared session.")
                own_id = own[-1]["id"]
                last_reply = max((event["id"] for event in observed if event["id"] < own_id and
                                  event["speaker"] in ("Assistant", "Error")), default=0)
                legacy_position = (last_reply, sum(event["speaker"] in ("You", "Codex") and
                                                   last_reply < event["id"] <= own_id for event in observed))
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                state = api(config, "/api/state")
                new = [event for event in state["events"] if event["id"] > cursor]
                show_events(new, request_id=request_id)
                if new:
                    cursor = max(event["id"] for event in new)
                if request_id is not None and any(event["speaker"] in ("Assistant", "Error") and
                                                  event.get("requestId") == request_id for event in new):
                    return
                if legacy_position is not None:
                    last_reply, position = legacy_position
                    completed = sum(event["speaker"] in ("Assistant", "Error") and
                                    event["id"] > last_reply for event in state["events"])
                    if completed >= position:
                        return
                time.sleep(0.5)
            raise RuntimeError("Timed out waiting for a reply. The session may still be working or awaiting approval.")
    elif args.command == "watch":
        state = api(config, "/api/state")
        cursor = max((event["id"] for event in state["events"]), default=0)
        print("Watching new events. Press Ctrl+C to stop.")
        while True:
            state = api(config, "/api/state")
            show_events(state["events"], after_id=cursor)
            if state["events"]:
                cursor = max(cursor, state["events"][-1]["id"])
            time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, KeyboardInterrupt) as exc:
        if str(exc):
            print(str(exc), file=sys.stderr)
        sys.exit(1)
