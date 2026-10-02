"""One goal job on the T3 job machine: plan, wait for USER plan approval, then run."""

from __future__ import annotations

from ..agent.engine import run_turn
from ..lifecycles import JOB_TERMINAL
from .plan import plan_job


def run_goal(session, job_id: str) -> None:
    """Drive one job; the running step is replaced by the task pipeline in T6 task 3."""
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
            model = session.model
            notes = list(session.notes)
            project_root = session.project_root
            goal = session.state.jobs.records[job_id].goal
        notes.extend([context] if (context := session.knowledge.context_for(goal)) else [])
        answer, _ = run_turn(
            goal, model, [], notes,
            session._tools_for(project_root, job_id, job_id),
            lambda result: session._record("system", "tool.result", {
                "display": {"speaker": "Tool", "text": result["message"]},
                "toolStatus": result["status"],
            }, job=job_id),
            cancelled=lambda: session.state.jobs.records[job_id].state == "cancelled",
        )
        with session.lock:
            if session.state.jobs.records[job_id].state == "running":
                session._record("system", "chat.reply", {
                    "display": {"speaker": "Assistant", "text": answer},
                }, job=job_id)
                session.transition_job(job_id, "done")
    except Exception as exc:
        with session.lock:
            if session.state.jobs.records[job_id].state not in JOB_TERMINAL:
                session.transition_job(job_id, "failed", reason=str(exc))
                session._record("system", "error", {
                    "display": {"speaker": "Error", "text": str(exc)},
                }, job=job_id)
    finally:
        if entered_running:
            with session.lock:
                session._active_turns -= 1
                session.busy = session._active_turns > 0
            session.knowledge.end_activity()
        if getattr(session._turn_local, "owns_slot", False):
            session._turn_local.owns_slot = False
            session._turn_slot.release()
