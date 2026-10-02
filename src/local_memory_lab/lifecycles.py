"""Event-backed job and task projections and their transition tables."""

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
TASK_TERMINAL = {"applied", "rejected", "failed", "cancelled"}
TASK_SPEC = ("title", "description", "target", "files", "check", "order")


def validate_task_transition(current: str | None, target: str, debug_rounds: int = 0) -> None:
    """Validate one task step against the contract table and the debug-round limit."""
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
    submitted_by: str = ""


@dataclass
class Jobs:
    """Current jobs, rebuilt by replaying their state events."""

    records: dict[str, Job] = field(default_factory=dict)

    def transition_data(self, job_id: str, target: str, *, goal: str = "",
                        reason: str = "", submitted_by: str = "") -> dict:
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
            if submitted_by:
                if submitted_by not in {"user", "agent"}:
                    raise ValueError("Choose the USER or AGENT submitter.")
                data["submittedBy"] = submitted_by
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
                             reason=data.get("reason", ""),
                             submitted_by=data.get("submittedBy", ""))
        previous = self.records.get(job_id)
        self.records[job_id] = Job(job_id, data["state"],
                                   data.get("goal", "") if previous is None else previous.goal,
                                   data.get("reason", ""),
                                   data.get("submittedBy", "") if previous is None else previous.submitted_by)

    def active_ids(self) -> list[str]:
        return [job_id for job_id, job in self.records.items() if job.state not in JOB_TERMINAL]

    def public(self) -> list[dict]:
        return [{"id": job.id, "state": job.state, "goal": job.goal,
                 **({"submittedBy": job.submitted_by} if job.submitted_by else {}),
                 **({"reason": job.reason} if job.reason else {})}
                for job in self.records.values()]


@dataclass
class TaskRecord:
    id: str
    job: str
    spec: dict
    state: str = "pending"
    reason: str = ""
    debug_round: int = 0
    failure: dict | None = None

    def public(self) -> dict:
        result = {"id": self.id, "job": self.job, **self.spec, "state": self.state,
                  "debugRound": self.debug_round}
        if self.reason:
            result["reason"] = self.reason
        if self.failure:
            result["failure"] = self.failure
        return result


@dataclass
class Tasks:
    """Current tasks, rebuilt from task.state events; the first event fixes the spec (D17)."""

    records: dict[str, TaskRecord] = field(default_factory=dict)

    def transition_data(self, task_id: str, target: str, *, spec: dict | None = None,
                        reason: str = "", failure: dict | None = None) -> dict:
        current = self.records.get(task_id)
        source = current.state if current else None
        validate_task_transition(source, target, current.debug_round if current else 0)
        data = {"state": target}
        if source is None:
            if not isinstance(spec, dict) or set(spec) != set(TASK_SPEC):
                raise ValueError("A new task needs exactly its immutable specification.")
            target_spec = spec["target"]
            if (not isinstance(target_spec, dict) or not isinstance(spec["files"], list) or
                    spec["files"] != [target_spec.get("path")] or not isinstance(spec["check"], str) or
                    not isinstance(spec["order"], int) or isinstance(spec["order"], bool)):
                raise ValueError("A task names one target, its file, a check and an order.")
            data.update(spec)
        elif spec is not None:
            raise ValueError("An approved task specification cannot change.")
        if target == "debugging":
            data["debugRound"] = current.debug_round + 1
        if reason:
            data["reason"] = reason
        if failure:
            data["failure"] = failure
        return data

    def apply(self, event: dict) -> None:
        if event["kind"] != "task.state":
            return
        task_id, job_id, data = event.get("task"), event.get("job"), event["data"]
        if not isinstance(task_id, str) or not task_id or not isinstance(job_id, str) or not job_id:
            raise ValueError("A task state event needs job and task IDs.")
        current = self.records.get(task_id)
        spec = {key: data[key] for key in TASK_SPEC if key in data}
        self.transition_data(task_id, data["state"], spec=spec if current is None else (spec or None),
                             reason=data.get("reason", ""), failure=data.get("failure"))
        if current is None:
            current = self.records[task_id] = TaskRecord(task_id, job_id, spec)
        elif current.job != job_id:
            raise ValueError("A task belongs to one job.")
        current.state = data["state"]
        current.reason = data.get("reason", "")
        current.failure = data.get("failure") or current.failure
        current.debug_round = data.get("debugRound", current.debug_round)

    def for_job(self, job_id: str) -> list[TaskRecord]:
        return sorted((task for task in self.records.values() if task.job == job_id),
                      key=lambda task: task.spec["order"])

    def active_ids(self) -> list[str]:
        return [task_id for task_id, task in self.records.items() if task.state not in TASK_TERMINAL]

    def public(self) -> list[dict]:
        return [task.public() for task in self.records.values()]
