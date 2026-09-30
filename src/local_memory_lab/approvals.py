"""Approval projection and its contract state transitions."""

from __future__ import annotations

from dataclasses import dataclass, field


RESOLUTIONS = {"approved", "rejected", "expired", "superseded"}


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

    def public(self, include_detail: bool) -> dict:
        title = "Apply project patch?" if self.kind == "patch" else self.summary
        result = {"id": self.id, "kind": self.kind, "title": title,
                  "name": self.summary, "state": self.state}
        if self.request_id:
            result["requestId"] = self.request_id
        if self.job:
            result["job"] = self.job
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
                request_id=data.get("requestId"),
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
