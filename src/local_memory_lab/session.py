"""Authoritative in-memory shared session and prompt queue."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from .agent.engine import DEFAULT_MODEL, MAX_RECENT_TURNS, installed_chat_models, run_turn
from .agent.file_tools import FileTools
from .agent.project_tools import ProjectTools
from .agent.tool_router import SharedTools
from .locations import OUTPUT, ROOT


@dataclass
class Approval:
    id: str
    request_id: str
    kind: str
    title: str
    name: str
    old_content: str = ""
    new_content: str = ""
    diff: str = ""
    decided: threading.Event = field(default_factory=threading.Event)
    approved: bool = False


class SharedSession:
    def __init__(self):
        self.lock = threading.RLock()
        self.prompts: queue.Queue[tuple[str, str, str]] = queue.Queue(maxsize=32)
        self.events: list[dict] = []
        self.next_event_id = 1
        self.turns: list[list[dict]] = []
        self.notes: list[str] = []
        self.busy = False
        self.pending: Approval | None = None
        self.file_tools = FileTools(OUTPUT)
        self.project_root: Path | None = None
        self.model = DEFAULT_MODEL
        self.models: list[str] = []
        self.model_error = ""
        try:
            self.models = installed_chat_models()
            if self.models and self.model not in self.models:
                self.model = self.models[0]
        except Exception as exc:
            self.model_error = str(exc)
        threading.Thread(target=self._work, daemon=True, name="shared-ollama-session").start()

    def _event(self, speaker: str, text: str, **extra):
        with self.lock:
            self.events.append({"id": self.next_event_id, "speaker": speaker, "text": text, **extra})
            self.next_event_id += 1
            self.events = self.events[-500:]

    def submit(self, prompt: str, actor: str):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 8000:
            raise ValueError("Prompt must contain 1 to 8,000 characters.")
        request_id = str(uuid4())
        try:
            self.prompts.put_nowait((request_id, prompt.strip(), actor))
        except queue.Full as exc:
            raise ValueError("The prompt queue is full; wait for a reply.") from exc
        self._event("You" if actor == "human" else "Codex", prompt.strip(), requestId=request_id)
        return request_id

    def snapshot(self, actor: str) -> dict:
        with self.lock:
            pending = None
            if self.pending:
                pending = {"id": self.pending.id, "requestId": self.pending.request_id,
                           "name": self.pending.name, "kind": self.pending.kind,
                           "title": self.pending.title}
                if actor == "human":
                    pending.update({"oldContent": self.pending.old_content,
                                    "newContent": self.pending.new_content,
                                    "diff": self.pending.diff})
            return {
                "events": list(self.events), "busy": self.busy,
                "queueLength": self.prompts.qsize(), "pendingApproval": pending,
                "model": self.model, "models": list(self.models),
                "modelError": self.model_error, "notes": list(self.notes),
                "projectRoot": str(self.project_root) if self.project_root else None,
                "appFolder": str(ROOT),
            }

    def set_project_root(self, raw_path: object):
        root = ProjectTools.choose_root(raw_path)
        with self.lock:
            if self.busy or not self.prompts.empty():
                raise ValueError("Wait for the current conversation to finish before changing projects.")
            self.project_root = root
        self._event("System", f"Project folder selected: {root}")

    def add_note(self, text: str):
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError("Note must contain 1 to 2,000 characters.")
        with self.lock:
            if len(self.notes) >= 50:
                raise ValueError("This session has reached its 50-note limit.")
            self.notes.append(text.strip())
        self._event("Memory", "Remembered: " + text.strip())

    def remove_note(self, index: int):
        with self.lock:
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(self.notes):
                raise ValueError("Choose an existing note.")
            note = self.notes.pop(index)
        self._event("Memory", "Forgot: " + note)

    def select_model(self, name: str):
        with self.lock:
            if name not in self.models:
                raise ValueError("Choose an installed chat model.")
            self.model = name
        self._event("System", "Model set to " + name)

    def approve(self, approval_id: str, approved: bool):
        with self.lock:
            pending = self.pending
            if pending is None or pending.id != approval_id or pending.decided.is_set():
                raise ValueError("This approval is no longer pending.")
            pending.approved = approved is True
            pending.decided.set()

    def _confirm_overwrite(self, request_id: str, name: str, old_content: str, new_content: str) -> bool:
        pending = Approval(str(uuid4()), request_id, "overwrite", "Overwrite file?", name,
                           old_content=old_content, new_content=new_content)
        return self._wait_for_approval(pending)

    def _confirm_patch(self, request_id: str, proposal: dict) -> bool:
        detail = proposal["name"] + "\n" + "\n".join(proposal["paths"])
        pending = Approval(str(uuid4()), request_id, "patch", proposal["title"], detail,
                           diff=proposal["diff"])
        return self._wait_for_approval(pending)

    def _wait_for_approval(self, pending: Approval) -> bool:
        with self.lock:
            self.pending = pending
        self._event("Approval", f"Review {pending.title.lower()} in the browser.",
                    requestId=pending.request_id)
        pending.decided.wait(timeout=300)
        with self.lock:
            if self.pending is pending:
                self.pending = None
        if not pending.decided.is_set():
            self._event("Approval", "Approval timed out; files were kept.", requestId=pending.request_id)
            return False
        return pending.approved

    def _work(self):
        while True:
            request_id, prompt, _actor = self.prompts.get()
            with self.lock:
                self.busy = True
                model = self.model
                notes = list(self.notes)
                turns = list(self.turns)
                project_root = self.project_root
            try:
                answer, turn = run_turn(prompt, model, turns, notes,
                                        SharedTools(self.file_tools, project_root,
                                                    lambda proposal: self._confirm_patch(request_id, proposal),
                                                    request_id),
                                        lambda name, old, new: self._confirm_overwrite(request_id, name, old, new),
                                        lambda result: self._event("Tool", result["message"],
                                                                   toolStatus=result["status"], requestId=request_id))
                with self.lock:
                    self.turns.append(turn)
                    self.turns = self.turns[-MAX_RECENT_TURNS:]
                self._event("Assistant", answer, requestId=request_id)
            except Exception as exc:
                self._event("Error", str(exc), requestId=request_id)
            finally:
                with self.lock:
                    self.busy = False
                self.prompts.task_done()


