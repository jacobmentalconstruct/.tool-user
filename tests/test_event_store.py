import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_memory_lab.event_store import EventStore


class EventStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "events.sqlite"
        self.store = EventStore(self.path)

    def test_append_increases_ids_and_cursor_returns_only_new_events(self):
        first = self.store.append("user", "chat.prompt", {"text": "hello"})
        second = self.store.append("agent", "note.added", {"text": "remember"})

        self.assertLess(first["id"], second["id"])
        self.assertEqual([second], self.store.read_after(first["id"]))
        self.assertEqual([], self.store.read_after(second["id"]))

    def test_event_rows_cannot_be_updated_or_deleted(self):
        event = self.store.append("system", "index.updated", {})

        with closing(sqlite3.connect(self.path)) as db:
            with db:
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("UPDATE events SET kind = 'error' WHERE id = ?", (event["id"],))
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute("DELETE FROM events WHERE id = ?", (event["id"],))

    def test_rejects_invalid_actor_and_cursor(self):
        with self.assertRaises(ValueError):
            self.store.append("human", "chat.prompt", {})
        with self.assertRaises(ValueError):
            self.store.read_after(-1)


if __name__ == "__main__":
    unittest.main()
