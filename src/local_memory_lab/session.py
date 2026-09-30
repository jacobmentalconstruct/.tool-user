"""Shared session coordinator around event-backed domains and workers."""

from __future__ import annotations

import queue
import json
import threading
from pathlib import Path
from uuid import uuid4

from .agent.engine import DEFAULT_MODEL, installed_chat_models, run_turn
from .agent.project_tools import ProjectTools
from .agent.tool_router import SharedTools
from .event_store import EventStore
from .command_runner import CommandRunner
from .lifecycles import JOB_TERMINAL
from .locations import CONTROL
from .knowledge.service import KnowledgeService
from .session_state import SessionState


class SharedSession:
    def __init__(self, store_path: Path | str | None = None, *,
                 load_models: bool = True, start_worker: bool = True):
        self.lock = threading.RLock()
        self.prompts: queue.Queue[int] = queue.Queue(maxsize=32)
        self.store = EventStore(store_path or CONTROL / "events.sqlite")
        self.state = SessionState.restore(self.store)
        self.knowledge = KnowledgeService(self.state.workspace.project_root)
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
            return self._record("system", "job.state", data, job=job_id)

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
            self.transition_job(job_id, "awaiting_plan_approval")
            approval_id = self.request_approval(
                "plan", "Approve goal plan", goal.strip(), actor="system", job=job_id)
            threading.Thread(target=self._run_goal, args=(job_id, approval_id),
                             daemon=True, name="goal-" + job_id[:8]).start()
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
                "jobs": self.state.jobs.public(),
            }

    def events_after(self, event_id: int) -> list[dict]:
        return self.store.read_after(event_id)

    def set_project_root(self, raw_path: object, actor: str = "USER"):
        actor_label = self._require_user(actor)
        root = ProjectTools.choose_root(raw_path)
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
                         actor: str, job: str | None = None,
                         request_id: str | None = None) -> str:
        if kind not in {"plan", "patch", "command"}:
            raise ValueError("Choose a contract approval kind.")
        if (kind == "plan" and actor != "system") or (kind != "plan" and actor != "role:builder"):
            raise ValueError("Only the lifecycle or a ROLE may request this approval.")
        approval_id = str(uuid4())
        with self.lock:
            self._approval_waiters[approval_id] = threading.Event()
            self._record(actor, "approval.requested", {
                "id": approval_id, "kind": kind, "summary": summary, "detail": detail,
                "state": "pending", "requestId": request_id,
                "display": {"speaker": "Approval", "text": f"Review {summary.lower()} in the browser."},
            }, job=job)
        return approval_id

    def _resolve_approval(self, approval_id: str, state: str, actor: str) -> None:
        with self.lock:
            pending = self.state.approvals.require_pending(approval_id)
            self._record(actor, "approval.resolved", {
                "id": approval_id, "state": state, "approved": state == "approved",
                "requestId": pending.request_id,
            }, job=pending.job)
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

    def _confirm_patch(self, request_id: str, proposal: dict,
                       job_id: str | None = None) -> bool:
        approval_id = self.request_approval("patch", proposal["name"], proposal["diff"],
                                            actor="role:builder", request_id=request_id,
                                            job=job_id)
        return self.wait_for_approval(approval_id)

    def _run_named_command(self, name: str, project_root: Path | None,
                           request_id: str, job_id: str | None = None) -> dict:
        if project_root is None:
            raise ValueError("Choose a project before running commands.")
        runner = CommandRunner(project_root)
        spec = runner.resolve(name)
        detail = json.dumps({"argv": list(spec.argv), "cwd": str(spec.root)}, indent=2)
        approval_id = self.request_approval(
            "command", f"Run {name}?", detail, actor="role:builder",
            request_id=request_id, job=job_id)
        if not self.wait_for_approval(approval_id):
            return {"status": "cancelled", "message": f"Command {name} was not approved."}
        cancelled = (lambda: self.state.jobs.records[job_id].state == "cancelled") if job_id else None
        result = runner.run(spec, cancelled=cancelled)
        self._record("system", "command.result", result, job=job_id)
        message = f"Command {name}: {result['status']} (exit {result['exit_code']})."
        if result["output"]:
            message += "\n" + result["output"]
        return {"status": "ok" if result["status"] == "ok" else result["status"],
                "message": message, **result}

    def _run_goal(self, job_id: str, approval_id: str) -> None:
        entered_running = False
        try:
            approved = self.wait_for_approval(approval_id)
            with self.lock:
                if self.state.jobs.records[job_id].state in JOB_TERMINAL:
                    return
                if not approved:
                    resolution = self.state.approvals.records[approval_id].state
                    if resolution == "rejected":
                        self.transition_job(job_id, "rejected")
                    else:
                        self.transition_job(job_id, "failed", reason="plan approval expired")
                    return
            self._turn_slot.acquire()
            self._turn_local.owns_slot = True
            with self.lock:
                if self.state.jobs.records[job_id].state in JOB_TERMINAL:
                    return
                self.transition_job(job_id, "running")
                self._active_turns += 1
                self.busy = True
                entered_running = True
                model = self.model
                notes = [*self.notes, self.knowledge.context_for(goal)]
                project_root = self.project_root
                goal = self.state.jobs.records[job_id].goal
            answer, _ = run_turn(
                goal, model, [], notes,
                SharedTools(project_root,
                            lambda proposal: self._confirm_patch(job_id, proposal, job_id),
                            job_id,
                            lambda name: self._run_named_command(name, project_root, job_id, job_id)),
                lambda result: self._record("system", "tool.result", {
                    "display": {"speaker": "Tool", "text": result["message"]},
                    "toolStatus": result["status"],
                }, job=job_id),
                cancelled=lambda: self.state.jobs.records[job_id].state == "cancelled",
            )
            with self.lock:
                if self.state.jobs.records[job_id].state == "running":
                    self._record("system", "chat.reply", {
                        "display": {"speaker": "Assistant", "text": answer},
                    }, job=job_id)
                    self.transition_job(job_id, "done")
        except Exception as exc:
            with self.lock:
                if self.state.jobs.records[job_id].state not in JOB_TERMINAL:
                    self.transition_job(job_id, "failed", reason=str(exc))
                    self._record("system", "error", {
                        "display": {"speaker": "Error", "text": str(exc)},
                    }, job=job_id)
        finally:
            if entered_running:
                with self.lock:
                    self._active_turns -= 1
                    self.busy = self._active_turns > 0
            if getattr(self._turn_local, "owns_slot", False):
                self._turn_local.owns_slot = False
                self._turn_slot.release()

    def _work(self):
        while True:
            prompt_event_id = self.prompts.get()
            self._turn_slot.acquire()
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
                notes = [*self.notes, self.knowledge.context_for(prompt)]
                turns = list(self.turns)
                project_root = self.project_root
            answer, turn = run_turn(prompt, model, turns, notes,
                                    SharedTools(project_root,
                                                lambda proposal: self._confirm_patch(request_id, proposal),
                                                request_id,
                                                lambda name: self._run_named_command(
                                                    name, project_root, request_id)),
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
                self._active_turns -= 1
                self.busy = self._active_turns > 0
            self.prompts.task_done()
            self._turn_local.owns_slot = False
            self._turn_slot.release()
