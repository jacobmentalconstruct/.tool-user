"""One goal job: plan, wait for USER plan approval, then run each task through the team (fail fast)."""

from __future__ import annotations

from ..lifecycles import JOB_TERMINAL
from ..locations import CONTROL
from ..workspace.backups import BackupStore
from ..workspace.patching import staged_apply
from .pipeline import TaskCancelled, TaskFailed, run_task
from .plan import plan_job
from .roles import load_roles


def run_goal(session, job_id: str, roles: dict | None = None) -> None:
    """Drive one job; any failed, rejected or exhausted task fails the job and later tasks do not run."""
    entered_running = False
    try:
        with session._turn_slot:  # planning is a model step, so it takes the one turn slot
            session.knowledge.begin_activity()  # waits for a full index sync first
            try:
                approval_id = plan_job(session, job_id)
            finally:
                session.knowledge.end_activity()
        if approval_id is None:
            return
        approved = session.wait_for_approval(approval_id)
        with session.lock:
            if session.state.jobs.records[job_id].state in JOB_TERMINAL:
                return
            if not approved:
                resolution = session.state.approvals.records[approval_id].state
                if resolution == "rejected":
                    session.transition_job(job_id, "rejected")
                else:
                    session.transition_job(job_id, "failed", reason="plan approval expired")
                return
        session._turn_slot.acquire()
        session._turn_local.owns_slot = True
        with session.lock:
            if session.state.jobs.records[job_id].state in JOB_TERMINAL:
                return
            session.knowledge.begin_activity()
            session.transition_job(job_id, "running")
            session._active_turns += 1
            session.busy = True
            entered_running = True
            project_root = session.project_root
        roles = roles or load_roles()
        for task in session.state.tasks.for_job(job_id):
            if not _run_one(session, job_id, task, project_root, roles):
                return
        with session.lock:
            if session.state.jobs.records[job_id].state == "running":
                session.transition_job(job_id, "done")
    except Exception as exc:
        with session.lock:
            if session.state.jobs.records[job_id].state not in JOB_TERMINAL:
                session.transition_job(job_id, "failed", reason=str(exc))
                session._record("system", "error", {"display": {"speaker": "Error", "text": str(exc)}}, job=job_id)
    finally:
        if entered_running:
            with session.lock:
                session._active_turns -= 1
                session.busy = session._active_turns > 0
            session.knowledge.end_activity()
        if getattr(session._turn_local, "owns_slot", False):
            session._turn_local.owns_slot = False
            session._turn_slot.release()


def _run_one(session, job_id: str, task, project_root, roles: dict) -> bool:
    """Run one task to its gate, then the USER's patch approval and the transactional apply."""
    def step(state: str, **extra) -> None:
        with session.lock:
            session._record("system", "task.state", session.state.tasks.transition_data(task.id, state, **extra),
                            job=job_id, task=task.id)

    def fail(reason: str, message: str, failure: dict | None = None) -> bool:
        with session.lock:
            if session.state.jobs.records[job_id].state in JOB_TERMINAL:
                return False
            step("failed", reason=reason, failure=failure or {"reason": reason, "error": message})
            session.transition_job(job_id, "failed", reason=f"task {task.spec['order']} {reason}: {message}")
        return False

    cancelled = lambda: session.state.jobs.records[job_id].state == "cancelled"  # noqa: E731
    on_command = lambda result: session._record("system", "command.result", result, job=job_id, task=task.id)  # noqa: E731
    try:
        result = run_task(task, project_root, CONTROL / "scratch" / job_id / task.id, roles, session.knowledge,
                          step=step, cancelled=cancelled, on_command=on_command)
    except TaskCancelled:
        return False
    except TaskFailed as exc:
        return fail(exc.reason, str(exc), exc.failure)
    try:
        with session.lock:
            if session.state.jobs.records[job_id].state in JOB_TERMINAL:
                return False
            step("awaiting_approval")
            approval_id = session.request_approval(
                "patch", f"Task {task.spec['order']}: {task.spec['title']}", result.gate.diff, actor="system",
                job=job_id, task=task.id, origin_role=result.origin_role, candidate=result.candidate)
        if not session.wait_for_approval(approval_id):
            state = session.state.approvals.records[approval_id].state
            if state == "rejected":
                with session.lock:
                    if session.state.jobs.records[job_id].state not in JOB_TERMINAL:
                        step("rejected", reason="patch_rejected")
                        session.transition_job(job_id, "failed", reason=f"task {task.spec['order']} rejected by USER")
                return False
            return fail("approval_expired", "patch approval expired") if state == "expired" else False
        try:
            applied, backup = staged_apply(result.workspace.changes(), BackupStore.for_project(CONTROL, project_root),
                                           task.id)
        except ValueError as exc:  # the live file changed after the copy: a stale candidate is never applied
            return fail("stale_candidate", str(exc))
        with session.lock:
            step("applied")
            session._record("system", "tool.result", {"display": {"speaker": "Tool", "text":
                            f"Applied task {task.spec['order']} to {', '.join(applied)}; backup {backup}"},
                            "paths": applied, "backup": backup}, job=job_id, task=task.id)
        session.knowledge.index_paths_now(applied)  # the next task's context sees this change
        return True
    finally:
        result.workspace.discard()
