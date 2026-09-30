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
        data = event.get("data", {})
        if request_id is not None and data.get("requestId") != request_id:
            continue
        display = data.get("display")
        if display:
            print(f"[{display['speaker']}] {display['text']}", flush=True)
        if event["kind"] == "job.state":
            print(f"[Job {event['job']}] {data['state']}", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Talk to the one running Local Memory Lab session")
    sub = parser.add_subparsers(dest="command", required=True)
    send = sub.add_parser("send", help="Queue a prompt")
    send.add_argument("text")
    send.add_argument("--wait", action="store_true", help="Watch until the reply arrives")
    send.add_argument("--timeout", type=int, default=180)
    goal = sub.add_parser("goal", help="Submit a New goal")
    goal.add_argument("text")
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
            print("Waiting for USER approval:", state["pendingApproval"]["name"])
        for job in state.get("jobs", []):
            print(f"Job {job['id']}: {job['state']} — {job['goal']}")
        print(f"Event cursor: {state['lastEventId']} | Notes: {len(state['notes'])}")
    elif args.command == "remember":
        api(config, "/api/notes", {"text": args.text})
        print("Note added to the shared session.")
    elif args.command == "send":
        before = api(config, "/api/state")
        cursor = before["lastEventId"]
        response = api(config, "/api/messages", {"text": args.text})
        request_id = response.get("requestId")
        if not isinstance(request_id, str) or not request_id:
            raise RuntimeError("Hub did not return a request ID.")
        print("Prompt queued.", flush=True)
        if args.wait:
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                response = api(config, f"/api/events?after={cursor}")
                new = response["events"]
                show_events(new, request_id=request_id)
                if new:
                    cursor = max(cursor, max(event["id"] for event in new))
                if any(event["kind"] in ("chat.reply", "error") and
                       event["data"].get("requestId") == request_id for event in new):
                    return
                time.sleep(0.5)
            raise RuntimeError("Timed out waiting for a reply. The session may still be working or awaiting approval.")
    elif args.command == "goal":
        response = api(config, "/api/goals", {"text": args.text})
        print("New goal queued:", response["jobId"])
    elif args.command == "watch":
        state = api(config, "/api/state")
        cursor = state["lastEventId"]
        print("Watching new events. Press Ctrl+C to stop.")
        while True:
            response = api(config, f"/api/events?after={cursor}")
            show_events(response["events"], after_id=cursor)
            if response["events"]:
                cursor = max(cursor, max(event["id"] for event in response["events"]))
            time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, KeyboardInterrupt) as exc:
        if str(exc):
            print(str(exc), file=sys.stderr)
        sys.exit(1)
