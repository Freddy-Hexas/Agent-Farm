"""Session event projections used by the runtime and compatibility APIs.

The SQLite store owns ordering and durability.  This module deliberately keeps
projection logic pure so a timeline or summary can be rebuilt from a cursor
without starting a harness or contacting a provider.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any, Iterable


TERMINAL_STATUSES = {
    "completed",
    "cancelled",
    "interrupted",
    "failed",
    "blocked",
}
KNOWN_STATUSES = {
    "created",
    "queued",
    "running",
    "cancelling",
    "completed",
    "cancelled",
    "interrupted",
    "failed",
    "blocked",
}

STOP_REASONS = {
    "completed": "completed",
    "cancelled": "cancelled",
    "interrupted": "cancelled",
    "failed": "failed",
    "blocked": "blocked",
}


def _event_type(event: dict[str, Any]) -> str:
    return str(event.get("event_type") or event.get("type") or "event")


def _status_from_event(event: dict[str, Any]) -> str | None:
    payload = event.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    for candidate in (payload.get("status"), event.get("status")):
        if isinstance(candidate, str) and candidate.lower() in KNOWN_STATUSES:
            return candidate.lower()
    event_type = _event_type(event)
    suffix = event_type.rsplit("/", 1)[-1].lower()
    if suffix in {"started", "resumed"}:
        return "running"
    if suffix in {"spawned", "forked"}:
        return "queued"
    if suffix in TERMINAL_STATUSES:
        return suffix
    if suffix in {"cancel_requested", "interrupt_requested"}:
        return "cancelling"
    return None


def project_session_events(
    events: Iterable[dict[str, Any]],
    *,
    session: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a small, JSON-safe session projection from ordered events."""

    ordered = sorted(
        (deepcopy(event) for event in events),
        key=lambda event: int(event.get("event_seq", event.get("sequence", 0)) or 0),
    )
    counts = Counter(_event_type(event) for event in ordered)
    status = (session or {}).get("status") or "created"
    stop_reason = (session or {}).get("stop_reason")
    latest_error: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = []
    for event in ordered:
        candidate = _status_from_event(event)
        if candidate:
            status = candidate
        payload = event.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        if _event_type(event) in {"session/failed", "job.failed", "worker.failed"}:
            latest_error = {
                "type": payload.get("error_type") or event.get("error_type") or "Failed",
                "message": payload.get("message") or event.get("error") or payload.get("error"),
            }
        for key in ("evidence", "report", "result", "deliverable", "summary"):
            value = payload.get(key, event.get(key))
            if value is not None:
                evidence.append({"event_seq": event.get("event_seq"), "type": key, "value": deepcopy(value)})
        if status in TERMINAL_STATUSES:
            stop_reason = stop_reason or STOP_REASONS.get(status)
    return {
        "status": status,
        "stop_reason": stop_reason,
        "event_count": len(ordered),
        "last_event_seq": ordered[-1].get("event_seq", ordered[-1].get("sequence", 0)) if ordered else 0,
        "event_types": dict(counts),
        "latest_error": latest_error,
        "evidence": evidence[-20:],
        "timeline": ordered,
    }


def project_thread_from_events(
    thread: dict[str, Any],
    events: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Return a compatibility Thread projection with ledger events attached."""

    projected = deepcopy(thread)
    ordered = sorted(
        (deepcopy(event) for event in events),
        key=lambda event: int(event.get("event_seq", event.get("sequence", 0)) or 0),
    )
    projected["events"] = [
        {
            "schema_version": int(event.get("schema_version", 1)),
            "sequence": int(event.get("event_seq", event.get("sequence", 0)) or 0),
            "type": _event_type(event),
            "created_at": event.get("created_at") or event.get("timestamp"),
            "turn_id": event.get("turn_id"),
            "item_id": event.get("item_id"),
            "payload": deepcopy(event.get("payload") or {}),
        }
        for event in ordered
    ]
    return projected


def project_job_from_events(
    job: dict[str, Any],
    events: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Return a job summary whose lifecycle is derived from its event ledger."""

    ordered = list(events)
    lifecycle_events = [
        event
        for event in ordered
        if _event_type(event).startswith("session/")
        or _event_type(event).startswith("job.")
        or _event_type(event).startswith("farm.")
        or _event_type(event).startswith("task.")
    ]
    projection = project_session_events(lifecycle_events, session=job)
    projected = deepcopy(job)
    status = projection["status"]
    if isinstance(status, str) and status.islower():
        status = status.upper()
    if status and status not in {"CREATED", "QUEUED", "RUNNING"}:
        projected["status"] = status
    if projection["stop_reason"]:
        projected["stop_reason"] = projection["stop_reason"]
    projected["event_count"] = len(ordered)
    projected["event_types"] = dict(Counter(_event_type(event) for event in ordered))
    projected["last_event_seq"] = max(
        (int(event.get("event_seq", event.get("sequence", 0)) or 0) for event in ordered),
        default=0,
    )
    return projected
