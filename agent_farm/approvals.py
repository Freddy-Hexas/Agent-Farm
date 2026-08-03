from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from .protocol import PROTOCOL_VERSION


APPROVAL_DECISIONS = frozenset({"allow_once", "allow_session", "deny", "cancel"})


class ApprovalError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ApprovalBroker:
    """Coordinates blocking tool approvals between Agent threads and UI clients."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._requests: dict[str, dict[str, Any]] = {}
        self._session_grants: set[tuple[str, str, str]] = set()
        self._closed = False

    def request(
        self,
        *,
        job_kind: str,
        job_id: str,
        request: dict[str, Any],
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        scope = request.get("scope")
        if not isinstance(scope, str) or not scope:
            raise ApprovalError("Approval requests require a non-empty scope.")
        grant_key = (job_kind, job_id, scope)
        with self._condition:
            if self._closed:
                raise ApprovalError("The approval broker is shutting down.")
            if grant_key in self._session_grants:
                return "allow_session"
            approval_id = uuid.uuid4().hex
            record = {
                "protocol_version": PROTOCOL_VERSION,
                "approval_id": approval_id,
                "job_kind": job_kind,
                "job_id": job_id,
                "status": "pending",
                "decision": None,
                "created_at": _utc_now(),
                "resolved_at": None,
                **request,
            }
            self._requests[approval_id] = record

        if event_callback is not None:
            event_callback({"type": "approval.requested", "approval": dict(record)})

        with self._condition:
            self._condition.wait_for(
                lambda: self._requests[approval_id]["status"] != "pending"
            )
            resolved = dict(self._requests[approval_id])

        if event_callback is not None:
            event_callback({"type": "approval.resolved", "approval": resolved})
        decision = resolved.get("decision")
        if decision not in APPROVAL_DECISIONS:
            raise ApprovalError("The approval request ended without a valid decision.")
        return str(decision)

    def list(self, *, status: str | None = None) -> list[dict[str, Any]]:
        with self._condition:
            records = [dict(record) for record in self._requests.values()]
        if status is not None:
            records = [record for record in records if record.get("status") == status]
        return sorted(records, key=lambda record: str(record.get("created_at") or ""))

    def get(self, approval_id: str) -> dict[str, Any]:
        with self._condition:
            try:
                return dict(self._requests[approval_id])
            except KeyError as exc:
                raise FileNotFoundError("Unknown approval request.") from exc

    def respond(self, approval_id: str, decision: str) -> dict[str, Any]:
        if decision not in APPROVAL_DECISIONS:
            raise ValueError(
                "decision must be allow_once, allow_session, deny, or cancel."
            )
        with self._condition:
            try:
                record = self._requests[approval_id]
            except KeyError as exc:
                raise FileNotFoundError("Unknown approval request.") from exc
            if record["status"] != "pending":
                raise ApprovalError("The approval request has already been resolved.")
            record["status"] = "resolved"
            record["decision"] = decision
            record["resolved_at"] = _utc_now()
            if decision == "allow_session":
                self._session_grants.add(
                    (str(record["job_kind"]), str(record["job_id"]), str(record["scope"]))
                )
            resolved = dict(record)
            self._condition.notify_all()
            return resolved

    def cancel_job(
        self,
        job_kind: str,
        job_id: str,
        *,
        agent_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._condition:
            now = _utc_now()
            cancelled: list[dict[str, Any]] = []
            for record in self._requests.values():
                if (
                    record["status"] == "pending"
                    and record["job_kind"] == job_kind
                    and record["job_id"] == job_id
                    and (agent_id is None or record.get("agent_id") == agent_id)
                ):
                    record["status"] = "resolved"
                    record["decision"] = "cancel"
                    record["resolved_at"] = now
                    cancelled.append(dict(record))
            if cancelled:
                self._condition.notify_all()
            return cancelled

    def close(self) -> None:
        with self._condition:
            self._closed = True
            now = _utc_now()
            for record in self._requests.values():
                if record["status"] == "pending":
                    record["status"] = "resolved"
                    record["decision"] = "cancel"
                    record["resolved_at"] = now
            self._condition.notify_all()
