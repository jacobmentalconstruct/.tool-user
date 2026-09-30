"""Event-backed job projection and the T3 transition tables."""

from __future__ import annotations

from dataclasses import dataclass, field


JOB_NEXT = {
    None: {"queued"},
    "queued": {"planning", "failed", "cancelled"},
    "planning": {"awaiting_plan_approval", "failed", "cancelled"},
    "awaiting_plan_approval": {"running", "rejected", "failed", "cancelled"},
    "running": {"done", "failed", "cancelled"},
}
JOB_TERMINAL = {"done", "failed", "cancelled", "rejected"}

TASK_NEXT = {
    None: {"pending"},
    "pending": {"building", "failed", "cancelled"},
    "building": {"testing", "failed", "cancelled"},
    "testing": {"debugging", "reviewing", "failed", "cancelled"},
    "debugging": {"testing", "failed", "cancelled"},
    "reviewing": {"gated", "failed", "cancelled"},
    "gated": {"awaiting_approval", "failed", "cancelled"},
    "awaiting_approval": {"applied", "rejected", "failed", "cancelled"},
}


def validate_task_transition(current: str | None, target: str, debug_rounds: int = 0) -> None:
    """Validate a future task step without creating or running a task in T3."""
    if target not in TASK_NEXT.get(current, set()):
        raise ValueError(f"Invalid task transition: {current} -> {target}.")
    if target == "debugging" and debug_rounds >= 2:
        raise ValueError("A task has exhausted its two debugging rounds.")


@dataclass
class Job:
    id: str
    state: str
    goal: str
    reason: str = ""


@dataclass
class Jobs:
    """Current jobs, rebuilt by replaying their state events."""

    records: dict[str, Job] = field(default_factory=dict)

    def transition_data(self, job_id: str, target: str, *, goal: str = "",
                        reason: str = "") -> dict:
        if not isinstance(job_id, str) or not job_id:
            raise ValueError("Choose a job ID.")
        current = self.records.get(job_id)
        source = current.state if current else None
        if target not in JOB_NEXT.get(source, set()):
            raise ValueError(f"Invalid job transition: {source} -> {target}.")
        if source is None and (not isinstance(goal, str) or not goal.strip()):
            raise ValueError("A queued job needs a goal.")
        data = {"state": target}
        if source is None:
            data["goal"] = goal.strip()
        if reason:
            data["reason"] = reason
        return data

    def apply(self, event: dict) -> None:
        if event["kind"] != "job.state":
            return
        job_id = event.get("job")
        data = event["data"]
        if not isinstance(job_id, str) or not job_id:
            raise ValueError("A job state event needs a job ID.")
        self.transition_data(job_id, data["state"], goal=data.get("goal", ""),
                             reason=data.get("reason", ""))
        previous = self.records.get(job_id)
        self.records[job_id] = Job(job_id, data["state"],
                                   data.get("goal", "") if previous is None else previous.goal,
                                   data.get("reason", ""))

    def active_ids(self) -> list[str]:
        return [job_id for job_id, job in self.records.items() if job.state not in JOB_TERMINAL]

    def public(self) -> list[dict]:
        return [{"id": job.id, "state": job.state, "goal": job.goal,
                 **({"reason": job.reason} if job.reason else {})}
                for job in self.records.values()]
