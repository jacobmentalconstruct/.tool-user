"""Shared session coordinator around event-backed domains and workers."""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from uuid import uuid4

from .agent.chat import DEFAULT_MODEL, answer, installed_chat_models
from .event_store import EventStore
from .approvals import request_data
from .lifecycles import JOB_TERMINAL, TASK_TERMINAL
from .locations import CONTROL
from .knowledge.service import KnowledgeService
from .session_state import SessionState
from .workspace.paths import choose_root
from .workspace.scratch import discard_job_scratch
from .team import draft as goal_draft
from .team.jobs import run_goal
from .team.roles import load_roles


class SharedSession:
    def __init__(self, store_path: Path | str | None = None, *, load_models: bool = True,
                 start_worker: bool = True, knowledge_embedder=None):
        self.lock = threading.RLock()
        self.prompts: queue.Queue[int] = queue.Queue(maxsize=32)
        self.store = EventStore(store_path or CONTROL / "events.sqlite")
        self.state = SessionState.restore(self.store)
        self.knowledge = KnowledgeService(self.state.workspace.project_root, embedder=knowledge_embedder, start_worker=start_worker)
        self.busy = False
        self._active_turns = 0
        self._turn_slot = threading.Semaphore(1)
        self._turn_local = threading.local()
        self._approval_waiters: dict[str, threading.Event] = {}
        self.models: list[str] = []
        self.model_error = ""
        if load_models:
            try:
                self.models = installed_chat_models()
            except Exception as exc:
                self.model_error = str(exc)
        for request_id in list(self.state.conversation.pending_prompts):
            self._record("system", "error", {"requestId": request_id,
                         "display": {"speaker": "Error", "text": "The session restarted before this reply completed."}})
        for pending in list(self.state.approvals.pending()):
            self._resolve_approval(pending.id, "expired", "system")
        for job_id in self.state.jobs.active_ids():
            self.transition_job(job_id, "failed", reason="interrupted by restart")
            discard_job_scratch(CONTROL / "scratch", job_id)
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

    def _record(self, actor: str, kind: str, data: dict, *,
                job: str | None = None, task: str | None = None) -> dict:
        with self.lock:
            event = self.store.append(actor, kind, data, job=job, task=task)
            self.state.apply(event)
            return event

    def transition_job(self, job_id: str, target: str, *, goal: str = "",
                       reason: str = "", submitted_by: str = "") -> dict:
        with self.lock:
            data = self.state.jobs.transition_data(
                job_id, target, goal=goal, reason=reason, submitted_by=submitted_by)
            if target == "queued" and submitted_by:
                data["display"] = {"speaker": submitted_by.upper(), "text": "New goal: " + goal.strip()}
            event = self._record("system", "job.state", data, job=job_id)
            if target in JOB_TERMINAL:  # a finished job closes its unfinished tasks
                closed = "cancelled" if target in {"cancelled", "rejected"} else "failed"
                for task in self.state.tasks.for_job(job_id):
                    if task.state not in TASK_TERMINAL:
                        self._record("system", "task.state", self.state.tasks.transition_data(
                            task.id, closed, reason=reason or target), job=job_id, task=task.id)
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

    def draft_goal(self, source, actor: str) -> dict:
        """A validated goal draft for the USER's New goal box (D22): never a job, an approval or a chat turn."""
        self._require_user(actor)
        with self.lock:
            root, text = self.project_root, self._draft_source(source)
        if root is None:
            raise ValueError("Choose a project folder first.")
        if self._turn_slot.acquire(blocking=False):  # never wait behind a running job step
            try:
                self.knowledge.begin_activity()
                try:
                    record = goal_draft.draft_goal(text, root, self.knowledge, load_roles()["planner"])
                finally:
                    self.knowledge.end_activity()
            finally:
                self._turn_slot.release()
        else:
            record = goal_draft.busy_record(root)
        record["source"] = source
        # no "turn": a draft never enters the chat history (session_state keeps only replies with one)
        self._record("system", "chat.reply", {"display": {"speaker": "Assistant", "text": goal_draft.draft_text(record)},
                                              "goalDraft": record})
        return record

    def _draft_source(self, source) -> str:
        if source == "conversation":
            text = "\n".join(f"{message['role'].upper()}: {message['content']}"
                             for turn in self.turns for message in turn)
        elif isinstance(source, int) and not isinstance(source, bool):
            event = self.store.get(source)
            reply = event and event["kind"] == "chat.reply" and "goalDraft" not in event["data"]
            text = event["data"]["display"]["text"] if reply else ""
        else:
            raise ValueError("Draft from the conversation or from one assistant reply.")
        if not text.strip():
            raise ValueError("There is nothing to draft a goal from yet.")
        return text[-16_000:]

    def submit_goal(self, goal: str, actor: str) -> str:
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 8000:
            raise ValueError("Goal must contain 1 to 8,000 characters.")
        actor_label = self._actor_label(actor)
        with self.lock:
            if self.project_root is None:
                raise ValueError("Choose a project folder before submitting a goal.")
            if self.state.jobs.active_ids():
                raise ValueError("Wait for the current goal to finish.")
            job_id = str(uuid4())
            self.transition_job(job_id, "queued", goal=goal, submitted_by=actor_label)
            self.transition_job(job_id, "planning")
            threading.Thread(target=run_goal, args=(self, job_id), daemon=True,
                             name="goal-" + job_id[:8]).start()
            return job_id

    def snapshot(self, actor: str) -> dict:
        with self.lock:
            approvals = [item.public(actor == "USER") for item in self.state.approvals.pending()]
            return {
                "lastEventId": self.store.last_id, "busy": self.busy,
                "queueLength": self.prompts.qsize(),
                "pendingApproval": approvals[0] if approvals else None,
                "pendingApprovals": approvals,
                "model": self.model, "models": list(self.models),
                "modelError": self.model_error, "notes": self.notes,
                "projectRoot": str(self.project_root) if self.project_root else None,
                "jobs": self.state.jobs.public(), "tasks": self.state.tasks.public(),
            }

    def events_after(self, event_id: int) -> list[dict]:
        return self.store.read_after(event_id)

    def set_project_root(self, raw_path: object, actor: str = "USER"):
        actor_label = self._require_user(actor)
        root = choose_root(raw_path)
        with self.lock:
            if self.busy or not self.prompts.empty() or self.state.jobs.active_ids():
                raise ValueError("Wait for the current conversation to finish before changing projects.")
            self._record(actor_label, "project.selected", {
                "path": str(root),
                "display": {"speaker": "System", "text": f"Project folder selected: {root}"},
            })
            self.knowledge.set_project(root)

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
            self._resolve_approval(approval_id, "approved" if approved else "rejected", actor_label)

    def request_approval(self, kind: str, summary: str, detail: str, *,
                         actor: str, job: str | None = None, task: str | None = None,
                         request_id: str | None = None, origin_role: str | None = None,
                         candidate: str | None = None) -> str:
        approval_id = str(uuid4())
        data = request_data(approval_id, kind, summary, detail, actor=actor, request_id=request_id,
                            origin_role=origin_role, candidate=candidate)
        with self.lock:
            self._approval_waiters[approval_id] = threading.Event()
            self._record(actor, "approval.requested", data, job=job, task=task)
        return approval_id

    def _resolve_approval(self, approval_id: str, state: str, actor: str) -> None:
        with self.lock:
            pending = self.state.approvals.require_pending(approval_id)
            self._record(actor, "approval.resolved", {
                "id": approval_id, "state": state, "approved": state == "approved",
                "requestId": pending.request_id,
            }, job=pending.job, task=pending.task)
            waiter = self._approval_waiters.get(approval_id)
            if waiter:
                waiter.set()

    def wait_for_approval(self, approval_id: str, timeout: float = 300) -> bool:
        with self.lock:
            waiter = self._approval_waiters[approval_id]
        parked = getattr(self._turn_local, "owns_slot", False)
        if parked:
            self._turn_local.owns_slot = False
            self._turn_slot.release()
        try:
            if not waiter.wait(timeout):
                with self.lock:
                    if self.state.approvals.records[approval_id].state == "pending":
                        self._resolve_approval(approval_id, "expired", "system")
        finally:
            if parked:
                self._turn_slot.acquire()
                self._turn_local.owns_slot = True
        with self.lock:
            self._approval_waiters.pop(approval_id, None)
            return self.state.approvals.records[approval_id].state == "approved"

    def cancel_job(self, job_id: str, actor: str = "USER") -> None:
        self._require_user(actor)
        with self.lock:
            self.transition_job(job_id, "cancelled", reason="cancelled by USER")
            for pending in list(self.state.approvals.pending()):
                if pending.job == job_id:
                    self._resolve_approval(pending.id, "superseded", "system")

    def _work(self):
        while True:
            prompt_event_id = self.prompts.get()
            self._turn_slot.acquire()
            self.knowledge.begin_activity()
            with self.lock:
                self._active_turns += 1
                self.busy = True
            threading.Thread(target=self._run_prompt, args=(prompt_event_id,), daemon=True).start()

    def _run_prompt(self, prompt_event_id: int) -> None:
        request_id = None
        self._turn_local.owns_slot = True
        try:
            prompt_event = self.store.get(prompt_event_id)
            if prompt_event is None or prompt_event["kind"] != "chat.prompt":
                return
            request_id = prompt_event["data"]["requestId"]
            prompt = prompt_event["data"]["display"]["text"]
            with self.lock:
                model = self.model
                notes = list(self.notes)
                turns = list(self.turns)
            notes.extend([context] if (context := self.knowledge.context_for(prompt)) else [])
            reply, turn = answer(prompt, model, turns, notes)  # answer-only: Chat never writes or runs commands
            with self.lock:
                self._record("system", "chat.reply", {
                    "display": {"speaker": "Assistant", "text": reply},
                    "requestId": request_id, "turn": turn,
                })
        except Exception as exc:
            self._record("system", "error", {"display": {"speaker": "Error", "text": str(exc)},
                                             "requestId": request_id})
        finally:
            with self.lock:
                self._active_turns -= 1; self.busy = self._active_turns > 0
            self.knowledge.end_activity()
            self.prompts.task_done()
            self._turn_local.owns_slot = False
            self._turn_slot.release()
