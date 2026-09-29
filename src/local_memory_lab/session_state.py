"""Domain-owned session projections rebuilt from the event stream."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .event_store import EventStore


@dataclass
class ConversationState:
    turns: list[list[dict]] = field(default_factory=list)
    pending_prompts: dict[str, int] = field(default_factory=dict)

    def apply(self, event: dict) -> None:
        data = event["data"]
        request_id = data.get("requestId")
        if event["kind"] == "chat.prompt" and isinstance(request_id, str):
            self.pending_prompts[request_id] = event["id"]
        if event["kind"] == "chat.reply" and isinstance(data.get("turn"), list):
            self.turns.append(data["turn"])
            self.turns = self.turns[-8:]
        if event["kind"] in {"chat.reply", "error"} and isinstance(request_id, str):
            self.pending_prompts.pop(request_id, None)


@dataclass
class NotesState:
    _notes: dict[str, str] = field(default_factory=dict)

    def apply(self, event: dict) -> None:
        if event["kind"] == "note.added":
            self._notes[event["data"]["id"]] = event["data"]["text"]
        elif event["kind"] == "note.removed":
            self._notes.pop(event["data"]["id"], None)

    @property
    def notes(self) -> list[str]:
        return list(self._notes.values())

    def note_at(self, index: int) -> tuple[str, str]:
        return list(self._notes.items())[index]


@dataclass
class WorkspaceState:
    project_root: Path | None = None
    model: str = ""

    def apply(self, event: dict) -> None:
        data = event["data"]
        if event["kind"] == "project.selected":
            self.project_root = Path(data["path"])
        elif event["kind"] == "model.selected":
            self.model = data["name"]


@dataclass
class SessionState:
    """Composes independently-owned projections for one event stream."""

    conversation: ConversationState = field(default_factory=ConversationState)
    notes: NotesState = field(default_factory=NotesState)
    workspace: WorkspaceState = field(default_factory=WorkspaceState)

    @classmethod
    def restore(cls, store: EventStore) -> "SessionState":
        state = cls()
        cursor = 0
        while True:
            events = store.read_after(cursor, limit=5000)
            if not events:
                return state
            for event in events:
                state.apply(event)
            cursor = events[-1]["id"]

    def apply(self, event: dict) -> None:
        self.conversation.apply(event)
        self.notes.apply(event)
        self.workspace.apply(event)
