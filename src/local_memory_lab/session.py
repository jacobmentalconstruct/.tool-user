"""Authoritative in-memory shared session and prompt queue."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from .agent.engine import DEFAULT_MODEL, installed_chat_models, run_turn
from .agent.project_tools import ProjectTools
from .agent.tool_router import SharedTools
from .event_store import EventStore
from .locations import CONTROL
from .session_state import SessionState


@dataclass
class Approval:
    id: str
    request_id: str
    kind: str
    title: str
    name: str
    diff: str = ""
    decided: threading.Event = field(default_factory=threading.Event)
    approved: bool = False


class SharedSession:
    def __init__(self, store_path: Path | str | None = None, *,
                 load_models: bool = True, start_worker: bool = True):
        self.lock = threading.RLock()
        self.prompts: queue.Queue[int] = queue.Queue(maxsize=32)
        self.store = EventStore(store_path or CONTROL / "events.sqlite")
        self.state = SessionState.restore(self.store)
        self.busy = False
        self.pending: Approval | None = None
        self.models: list[str] = []
        self.model_error = ""
        if load_models:
            try:
                self.models = installed_chat_models()
            except Exception as exc:
                self.model_error = str(exc)
        for request_id in list(self.state.conversation.pending_prompts):
            self._record("system", "error", {
                "requestId": request_id,
                "display": {"speaker": "Error", "text": "The session restarted before this reply completed."},
            })
        if start_worker:
            threading.Thread(target=self._work, daemon=True, name="shared-ollama-session").start()

    @property
    def turns(self) -> list[list[dict]]:
        return self.state.conversation.turns

    @property
    def notes(self) -> list[str]:
        return self.state.notes.notes

    @property
    def project_root(self) -> Path | None:
        return self.state.workspace.project_root

    @property
    def model(self) -> str:
        if self.state.workspace.model:
            return self.state.workspace.model
        if DEFAULT_MODEL in self.models:
            return DEFAULT_MODEL
        return self.models[0] if self.models else DEFAULT_MODEL

    def _record(self, actor: str, kind: str, data: dict) -> dict:
        with self.lock:
            event = self.store.append(actor, kind, data)
            self.state.apply(event)
            return event

    @staticmethod
    def _actor_label(actor: str) -> str:
        label = actor.lower() if isinstance(actor, str) else ""
        if label not in {"user", "agent"}:
            raise ValueError("Choose the USER or AGENT actor label.")
        return label

    @classmethod
    def _require_user(cls, actor: str) -> str:
        label = cls._actor_label(actor)
        if label != "user":
            raise ValueError("Only the USER may perform this action.")
        return label

    def _event(self, speaker: str, text: str, **extra):
        kind = {"Assistant": "chat.reply", "Tool": "tool.result", "Error": "error"}.get(speaker, "error")
        data = {"display": {"speaker": speaker, "text": text}, **extra}
        self._record("system", kind, data)

    def submit(self, prompt: str, actor: str):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 8000:
            raise ValueError("Prompt must contain 1 to 8,000 characters.")
        request_id = str(uuid4())
        actor_label = self._actor_label(actor)
        with self.lock:
            if self.prompts.full():
                raise ValueError("The prompt queue is full; wait for a reply.")
            event = self._record(actor_label, "chat.prompt", {
                "display": {"speaker": actor_label.upper(), "text": prompt.strip()},
                "requestId": request_id,
            })
            self.prompts.put_nowait(event["id"])
        return request_id

    def snapshot(self, actor: str) -> dict:
        with self.lock:
            pending = None
            if self.pending:
                pending = {"id": self.pending.id, "requestId": self.pending.request_id,
                           "name": self.pending.name, "kind": self.pending.kind,
                           "title": self.pending.title}
                if actor == "USER":
                    pending.update({"diff": self.pending.diff})
            return {
                "lastEventId": self.store.last_id, "busy": self.busy,
                "queueLength": self.prompts.qsize(), "pendingApproval": pending,
                "model": self.model, "models": list(self.models),
                "modelError": self.model_error, "notes": self.notes,
                "projectRoot": str(self.project_root) if self.project_root else None,
            }

    def events_after(self, event_id: int) -> list[dict]:
        return self.store.read_after(event_id)

    def set_project_root(self, raw_path: object, actor: str = "USER"):
        actor_label = self._require_user(actor)
        root = ProjectTools.choose_root(raw_path)
        with self.lock:
            if self.busy or not self.prompts.empty():
                raise ValueError("Wait for the current conversation to finish before changing projects.")
            self._record(actor_label, "project.selected", {
                "path": str(root),
                "display": {"speaker": "System", "text": f"Project folder selected: {root}"},
            })

    def add_note(self, text: str, actor: str = "USER"):
        actor_label = self._actor_label(actor)
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError("Note must contain 1 to 2,000 characters.")
        with self.lock:
            if len(self.notes) >= 50:
                raise ValueError("This session has reached its 50-note limit.")
            note_id = str(uuid4())
            self._record(actor_label, "note.added", {
                "id": note_id, "text": text.strip(),
                "display": {"speaker": "Memory", "text": "Remembered: " + text.strip()},
            })

    def remove_note(self, index: int, actor: str = "USER"):
        actor_label = self._actor_label(actor)
        with self.lock:
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(self.notes):
                raise ValueError("Choose an existing note.")
            note_id, note = self.state.notes.note_at(index)
            self._record(actor_label, "note.removed", {
                "id": note_id, "text": note,
                "display": {"speaker": "Memory", "text": "Forgot: " + note},
            })

    def select_model(self, name: str, actor: str = "USER"):
        actor_label = self._require_user(actor)
        with self.lock:
            if name not in self.models:
                raise ValueError("Choose an installed chat model.")
            self._record(actor_label, "model.selected", {
                "name": name, "display": {"speaker": "System", "text": "Model set to " + name},
            })

    def approve(self, approval_id: str, approved: bool, actor: str = "USER"):
        actor_label = self._require_user(actor)
        if not isinstance(approved, bool):
            raise ValueError("Approval decision must be true or false.")
        with self.lock:
            pending = self.pending
            if pending is None or pending.id != approval_id or pending.decided.is_set():
                raise ValueError("This approval is no longer pending.")
            pending.approved = approved
            self._record(actor_label, "approval.resolved", {
                "id": approval_id, "approved": approved,
            })
            pending.decided.set()

    def _confirm_patch(self, request_id: str, proposal: dict) -> bool:
        detail = proposal["name"] + "\n" + "\n".join(proposal["paths"])
        pending = Approval(str(uuid4()), request_id, "patch", proposal["title"], detail,
                           diff=proposal["diff"])
        return self._wait_for_approval(pending)

    def _wait_for_approval(self, pending: Approval) -> bool:
        with self.lock:
            self.pending = pending
            self._record("system", "approval.requested", {
                "id": pending.id, "kind": pending.kind, "title": pending.title,
                "name": pending.name, "requestId": pending.request_id,
                "display": {"speaker": "Approval", "text": f"Review {pending.title.lower()} in the browser."},
            })
        pending.decided.wait(timeout=300)
        with self.lock:
            if self.pending is pending:
                self.pending = None
        if not pending.decided.is_set():
            self._record("system", "approval.resolved", {
                "id": pending.id, "approved": False, "timedOut": True,
                "requestId": pending.request_id,
                "display": {"speaker": "Approval", "text": "Approval timed out; files were kept."},
            })
            return False
        return pending.approved

    def _work(self):
        while True:
            prompt_event_id = self.prompts.get()
            request_id = None
            try:
                prompt_event = self.store.get(prompt_event_id)
                if prompt_event is None or prompt_event["kind"] != "chat.prompt":
                    continue
                request_id = prompt_event["data"]["requestId"]
                prompt = prompt_event["data"]["display"]["text"]
                with self.lock:
                    self.busy = True
                    model = self.model
                    notes = list(self.notes)
                    turns = list(self.turns)
                    project_root = self.project_root
                answer, turn = run_turn(prompt, model, turns, notes,
                                        SharedTools(project_root,
                                                    lambda proposal: self._confirm_patch(request_id, proposal),
                                                    request_id),
                                        lambda result: self._event("Tool", result["message"],
                                                                   toolStatus=result["status"], requestId=request_id))
                with self.lock:
                    self._record("system", "chat.reply", {
                        "display": {"speaker": "Assistant", "text": answer},
                        "requestId": request_id, "turn": turn,
                    })
            except Exception as exc:
                self._event("Error", str(exc), requestId=request_id)
            finally:
                with self.lock:
                    self.busy = False
                self.prompts.task_done()


