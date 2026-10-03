"""Approval projection and its contract state transitions."""

from __future__ import annotations

from dataclasses import dataclass, field


RESOLUTIONS = {"approved", "rejected", "expired", "superseded"}
CANDIDATE_ROLES = {"role:builder", "role:debugger"}


def request_data(approval_id: str, kind: str, summary: str, detail: str, *, actor: str,
                 request_id: str | None = None, origin_role: str | None = None,
                 candidate: str | None = None) -> dict:
    """Validate who may request an approval and build its requested-event data (D18)."""
    if kind not in {"plan", "patch", "command"}:
        raise ValueError("Choose a contract approval kind.")
    gated_patch = kind == "patch" and actor == "system"
    if gated_patch and (origin_role not in CANDIDATE_ROLES or not candidate):
        raise ValueError("A gated patch approval names its originating role and candidate.")
    # Since T6 only the lifecycle asks: plan approvals, and patch approvals after the gate (reviewer L1).
    # Nothing requests a command approval: task checks run under plan approval in their workspace (D19).
    allowed = {"plan": {"system"}, "patch": {"system"}, "command": set()}[kind]
    if actor not in allowed:
        raise ValueError("Only the lifecycle may request this approval.")
    data = {"id": approval_id, "kind": kind, "summary": summary, "detail": detail,
            "state": "pending", "requestId": request_id,
            "display": {"speaker": "Approval", "text": f"Review {summary.lower()} in the browser."}}
    if gated_patch:
        data.update({"originRole": origin_role, "candidate": candidate})
    return data


@dataclass
class ApprovalRecord:
    id: str
    kind: str
    summary: str
    detail: str
    state: str = "pending"
    job: str | None = None
    task: str | None = None
    request_id: str | None = None
    origin_role: str | None = None
    candidate: str | None = None

    def public(self, include_detail: bool) -> dict:
        title = "Apply project patch?" if self.kind == "patch" else self.summary
        result = {"id": self.id, "kind": self.kind, "title": title,
                  "name": self.summary, "state": self.state}
        if self.request_id:
            result["requestId"] = self.request_id
        if self.job:
            result["job"] = self.job
        if self.task:
            result["task"] = self.task
        if self.origin_role:
            result.update({"originRole": self.origin_role, "candidate": self.candidate})
        if include_detail:
            result["detail"] = self.detail
            result["diff"] = self.detail if self.kind == "patch" else ""
        return result


@dataclass
class Approvals:
    """Durable current approval states, projected from requested/resolved events."""

    records: dict[str, ApprovalRecord] = field(default_factory=dict)

    def apply(self, event: dict) -> None:
        data = event["data"]
        if event["kind"] == "approval.requested":
            approval_id = data["id"]
            if approval_id in self.records:
                raise ValueError("Approval IDs must be unique.")
            self.records[approval_id] = ApprovalRecord(
                approval_id, data["kind"], data.get("summary", data.get("name", data.get("title", "Approval"))),
                data.get("detail", data.get("name", "")),
                job=event.get("job") or data.get("job"), task=event.get("task") or data.get("task"),
                request_id=data.get("requestId"), origin_role=data.get("originRole"),
                candidate=data.get("candidate"),
            )
        elif event["kind"] == "approval.resolved":
            approval = self.records.get(data["id"])
            if approval is None or approval.state != "pending":
                raise ValueError("This approval is no longer pending.")
            state = data.get("state")
            if state is None:  # T2 patch approval events used a boolean.
                state = "expired" if data.get("timedOut") else (
                    "approved" if data.get("approved") else "rejected")
            if state not in RESOLUTIONS:
                raise ValueError("Choose a contract approval resolution.")
            approval.state = state

    def pending(self) -> list[ApprovalRecord]:
        return [record for record in self.records.values() if record.state == "pending"]

    def require_pending(self, approval_id: str) -> ApprovalRecord:
        record = self.records.get(approval_id)
        if record is None or record.state != "pending":
            raise ValueError("This approval is no longer pending.")
        return record
