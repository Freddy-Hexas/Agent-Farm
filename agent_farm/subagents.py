"""Reusable child-session lifecycle for Supervisor-created Workers.

This service owns session metadata and authorization.  Harness-specific
execution remains behind the harness registry; a caller can therefore use the
same lifecycle for an in-process Worker, Codex compatibility run, or a future
ACP adapter.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .harnesses import HarnessCapabilityError, available_harnesses
from .runtime_store import RuntimeStore


SESSION_STATUSES = frozenset(
    {"created", "queued", "running", "cancelling", "completed", "cancelled", "interrupted", "failed", "blocked"}
)
TERMINAL_SESSION_STATUSES = frozenset({"completed", "cancelled", "interrupted", "failed", "blocked"})
PERMISSION_POLICIES = frozenset({"unattended-readonly", "one-shot", "inherited"})
SENSITIVE_ENV = re.compile(r"(?:API|AUTH|ACCESS|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|PRIVATE|KEY)", re.I)


class SessionOperationError(RuntimeError):
    pass


class SessionAuthorizationError(PermissionError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_stop_reason(status: str, *, reason: str | None = None) -> str | None:
    if reason in {"completed", "cancelled", "max_turns", "blocked", "failed"}:
        return reason
    return {
        "completed": "completed",
        "cancelled": "cancelled",
        "interrupted": "cancelled",
        "failed": "failed",
        "blocked": "blocked",
    }.get(status.lower())


def scrub_child_environment(
    source: Mapping[str, str] | None = None,
    *,
    allowed_keys: set[str] | frozenset[str] = frozenset(),
) -> dict[str, str]:
    """Return a child environment without credential-like variables by default."""

    values = dict(source or os.environ)
    return {
        key: value
        for key, value in values.items()
        if key in allowed_keys or not SENSITIVE_ENV.search(key)
    }


def _session_event(session: dict[str, Any], event_type: str, **payload: Any) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "type": event_type,
        "created_at": utc_now(),
        "session_id": session["session_id"],
        "parent_session_id": session.get("parent_session_id"),
        "farm_id": session.get("farm_id"),
        "thread_id": session.get("thread_id"),
        "turn_id": session.get("turn_id"),
        "correlation_id": session.get("correlation_id"),
        "payload": payload,
    }


class ChildSessionService:
    """Create and control child sessions with parent-lineage authorization."""

    def __init__(
        self,
        store: RuntimeStore,
        *,
        config_getter: Callable[[], Any] | None = None,
    ) -> None:
        self.store = store
        self._config_getter = config_getter

    def _config(self) -> Any:
        return self._config_getter() if self._config_getter is not None else None

    def _descriptor(self, harness_id: str) -> dict[str, Any]:
        config = self._config()
        for descriptor in available_harnesses(config):
            if descriptor["harness_id"] == harness_id:
                if not descriptor.get("available") or not descriptor.get("ready"):
                    raise SessionOperationError(descriptor.get("reason") or f"Harness is not ready: {harness_id}")
                return descriptor
        raise SessionOperationError(f"Unknown harness: {harness_id}")

    def _read(self, session_id: str) -> dict[str, Any]:
        return self.store.get_session(session_id)

    def _is_descendant(self, actor_session_id: str, target_session_id: str) -> bool:
        current = target_session_id
        visited: set[str] = set()
        while current and current not in visited:
            visited.add(current)
            if current == actor_session_id:
                return True
            try:
                current = str(self.store.get_session(current).get("parent_session_id") or "")
            except FileNotFoundError:
                return False
        return False

    def authorize(self, session_id: str, *, actor_session_id: str | None = None) -> dict[str, Any]:
        session = self._read(session_id)
        actor = actor_session_id or "user"
        if actor not in {"user", "system"} and not self._is_descendant(actor, session_id):
            raise SessionAuthorizationError("The actor does not own this session lineage.")
        return session

    def _append(self, session: dict[str, Any], event_type: str, **payload: Any) -> None:
        self.store.append_session_event(session["session_id"], _session_event(session, event_type, **payload))

    def spawn(
        self,
        parent_session_id: str,
        *,
        role: str = "worker",
        harness_id: str = "native",
        provider_id: str | None = None,
        model_id: str | None = None,
        route_id: str | None = None,
        permission_policy: str = "unattended-readonly",
        allowed_env_keys: set[str] | frozenset[str] = frozenset(),
        actor_session_id: str | None = None,
        request: str | None = None,
        farm_id: str | None = None,
        thread_id: str | None = None,
        turn_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        parent = self.authorize(parent_session_id, actor_session_id=actor_session_id)
        descriptor = self._descriptor(harness_id)
        if not descriptor["supports"].get("spawn", False):
            raise HarnessCapabilityError(f"Harness '{harness_id}' does not support spawn.")
        if permission_policy not in PERMISSION_POLICIES:
            raise ValueError("Unknown permission policy.")
        session_id = f"session-{uuid.uuid4().hex[:20]}"
        now = utc_now()
        child = {
            "schema_version": 1,
            "session_id": session_id,
            "parent_session_id": parent["session_id"],
            "farm_id": farm_id or parent.get("farm_id"),
            "thread_id": thread_id or parent.get("thread_id"),
            "turn_id": turn_id or parent.get("turn_id"),
            "role": role,
            "harness_id": harness_id,
            "provider_id": provider_id,
            "model_id": model_id,
            "route_id": route_id or (f"{provider_id or 'unconfigured'}/{model_id or 'unconfigured'}"),
            "status": "queued",
            "stop_reason": None,
            "permission_policy": permission_policy,
            "allowed_env_keys": sorted(allowed_env_keys),
            "request": request,
            "correlation_id": correlation_id,
            "created_at": now,
            "updated_at": now,
        }
        self.store.create_session(child)
        self._append(child, "session/spawned", status="queued", request=request, harness=descriptor["harness_id"])
        return self._read(session_id)

    def fork(
        self,
        parent_session_id: str,
        *,
        source_turn_id: str | None = None,
        actor_session_id: str | None = None,
    ) -> dict[str, Any]:
        parent = self.authorize(parent_session_id, actor_session_id=actor_session_id)
        if parent.get("status") not in {"completed", "failed", "blocked"}:
            raise SessionOperationError("A session can only be forked after it reaches a terminal turn.")
        harness_id = str(parent.get("harness_id") or "native")
        descriptor = self._descriptor(harness_id)
        if not descriptor["supports"].get("fork", False):
            raise HarnessCapabilityError(f"Harness '{harness_id}' does not support fork.")
        return self.spawn(
            parent_session_id,
            role=str(parent.get("role") or "worker"),
            harness_id=harness_id,
            provider_id=parent.get("provider_id"),
            model_id=parent.get("model_id"),
            route_id=parent.get("route_id"),
            actor_session_id=actor_session_id,
            request=f"Fork of {parent_session_id}",
            turn_id=source_turn_id,
        )

    def _transition(
        self,
        session_id: str,
        *,
        status: str,
        actor_session_id: str | None,
        event_type: str,
        stop_reason: str | None = None,
    ) -> dict[str, Any]:
        session = self.authorize(session_id, actor_session_id=actor_session_id)
        if status not in SESSION_STATUSES:
            raise ValueError("Unknown session status.")
        if session.get("status") == "completed" and status != "completed":
            raise SessionOperationError("A completed session cannot be changed.")
        updated = self.store.update_session(
            session_id,
            {
                "status": status,
                "stop_reason": stable_stop_reason(status, reason=stop_reason),
                "updated_at": utc_now(),
            },
        )
        self._append(updated, event_type, status=status, stop_reason=updated.get("stop_reason"))
        return self._read(session_id)

    def resume(self, session_id: str, *, actor_session_id: str | None = None) -> dict[str, Any]:
        session = self.authorize(session_id, actor_session_id=actor_session_id)
        if session.get("status") not in {"cancelled", "interrupted", "failed", "blocked"}:
            raise SessionOperationError("Only stopped sessions can be resumed.")
        return self._transition(
            session_id,
            status="queued",
            actor_session_id=actor_session_id,
            event_type="session/resumed",
        )

    def cancel(self, session_id: str, *, actor_session_id: str | None = None) -> dict[str, Any]:
        return self._transition(
            session_id,
            status="cancelled",
            actor_session_id=actor_session_id,
            event_type="session/cancelled",
            stop_reason="cancelled",
        )

    def request_cancel(self, session_id: str, *, actor_session_id: str | None = None) -> dict[str, Any]:
        """Record a cancellation request while an external runner winds down."""
        return self._transition(
            session_id,
            status="cancelling",
            actor_session_id=actor_session_id,
            event_type="session/cancel_requested",
        )

    def interrupt(self, session_id: str, *, actor_session_id: str | None = None) -> dict[str, Any]:
        return self._transition(
            session_id,
            status="interrupted",
            actor_session_id=actor_session_id,
            event_type="session/interrupted",
            stop_reason="cancelled",
        )

    def report(self, session_id: str, *, actor_session_id: str | None = None) -> dict[str, Any]:
        session = self.authorize(session_id, actor_session_id=actor_session_id)
        projection = self.store.session_projection(session_id)
        return {
            "schema_version": 1,
            "session_id": session_id,
            "parent_session_id": session.get("parent_session_id"),
            "role": session.get("role"),
            "harness_id": session.get("harness_id"),
            "provider_id": session.get("provider_id"),
            "model_id": session.get("model_id"),
            "route_id": session.get("route_id"),
            "status": session.get("status"),
            "stop_reason": session.get("stop_reason"),
            "event_count": projection["event_count"],
            "event_types": projection["event_types"],
            "latest_error": projection["latest_error"],
            # Evidence is deliberately bounded and excludes the raw transcript.
            "evidence": projection["evidence"],
        }

    def child_environment(self, session_id: str, *, actor_session_id: str | None = None) -> dict[str, str]:
        session = self.authorize(session_id, actor_session_id=actor_session_id)
        return scrub_child_environment(allowed_keys=set(session.get("allowed_env_keys") or []))
