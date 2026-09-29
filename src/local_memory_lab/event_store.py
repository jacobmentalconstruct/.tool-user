"""Append-only SQLite storage for shared session events."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


_ACTOR = re.compile(r"^(user|agent|system|role:(planner|builder|debugger|reviewer))$")
_KINDS = {
    "chat.prompt", "chat.reply", "note.added", "note.removed", "project.selected",
    "model.selected", "job.state", "task.state", "approval.requested", "approval.resolved",
    "tool.result", "command.result", "index.updated", "error",
}


class EventStore:
    """One durable event stream with monotonically increasing IDs."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    job TEXT,
                    task TEXT,
                    data TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS events_no_update
                BEFORE UPDATE ON events BEGIN
                    SELECT RAISE(ABORT, 'events are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS events_no_delete
                BEFORE DELETE ON events BEGIN
                    SELECT RAISE(ABORT, 'events are append-only');
                END;
                """
            )

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def append(self, actor: str, kind: str, data: dict, *,
               job: str | None = None, task: str | None = None) -> dict:
        if not isinstance(actor, str) or not _ACTOR.fullmatch(actor):
            raise ValueError("Choose a contract actor label.")
        if not isinstance(kind, str) or kind not in _KINDS:
            raise ValueError("Choose an event kind from the v0 contract.")
        if not isinstance(data, dict):
            raise ValueError("Event data must be an object.")
        for name, value in (("job", job), ("task", task)):
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{name} must be a non-empty string when present.")
        encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with self._lock, self._connection() as db:
            cursor = db.execute(
                "INSERT INTO events (ts, actor, kind, job, task, data) VALUES (?, ?, ?, ?, ?, ?)",
                (timestamp, actor, kind, job, task, encoded),
            )
            event_id = cursor.lastrowid
        return {"id": event_id, "ts": timestamp, "actor": actor, "kind": kind,
                **({"job": job} if job is not None else {}),
                **({"task": task} if task is not None else {}), "data": data}

    def read_after(self, event_id: int, limit: int = 500) -> list[dict]:
        if not isinstance(event_id, int) or isinstance(event_id, bool) or event_id < 0:
            raise ValueError("Event cursor must be a non-negative integer.")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 5000:
            raise ValueError("Event limit must be between 1 and 5,000.")
        with self._connection() as db:
            rows = db.execute(
                "SELECT id, ts, actor, kind, job, task, data FROM events WHERE id > ? ORDER BY id LIMIT ?",
                (event_id, limit),
            ).fetchall()
        result = []
        for row in rows:
            event = {"id": row["id"], "ts": row["ts"], "actor": row["actor"], "kind": row["kind"]}
            if row["job"] is not None:
                event["job"] = row["job"]
            if row["task"] is not None:
                event["task"] = row["task"]
            event["data"] = json.loads(row["data"])
            result.append(event)
        return result

    def get(self, event_id: int) -> dict | None:
        if not isinstance(event_id, int) or isinstance(event_id, bool) or event_id < 1:
            raise ValueError("Event ID must be a positive integer.")
        events = self.read_after(event_id - 1, limit=1)
        return events[0] if events and events[0]["id"] == event_id else None

    @property
    def last_id(self) -> int:
        with self._connection() as db:
            row = db.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()
        return row[0]
