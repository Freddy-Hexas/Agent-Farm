from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol


HARNESS_IDS = frozenset({"native", "codex"})
HARNESS_CAPABILITIES = frozenset(
    {
        "streaming",
        "tool_calls",
        "workspace_write",
        "approval_requests",
        "cancellation",
        "resumability",
    }
)


class HarnessError(RuntimeError):
    pass


class HarnessUnavailableError(HarnessError):
    pass


class HarnessCapabilityError(HarnessError):
    pass


@dataclass(frozen=True)
class HarnessDescriptor:
    harness_id: str
    display_name: str
    capabilities: tuple[str, ...]
    transports: tuple[str, ...]
    supports: dict[str, bool]
    available: bool = True
    ready: bool = True
    reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "harness_id": self.harness_id,
            "display_name": self.display_name,
            "version": "1",
            "capabilities": list(self.capabilities),
            "transports": list(self.transports),
            "supports": dict(self.supports),
            "available": self.available,
            "ready": self.ready,
        }
        if self.reason:
            data["reason"] = self.reason
        return data

    def supports_capabilities(self, required: set[str] | frozenset[str]) -> bool:
        return set(required).issubset(self.capabilities)


class HarnessRunner(Protocol):
    def __call__(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class HarnessRegistry:
    """Small provider-agnostic registry for executable agent loops."""

    descriptors: dict[str, HarnessDescriptor]
    runners: dict[str, HarnessRunner]

    def describe(self) -> list[dict[str, Any]]:
        return [self.descriptors[key].to_json() for key in sorted(self.descriptors)]

    def require(
        self,
        harness_id: str,
        *,
        required_capabilities: set[str] | frozenset[str] = frozenset(),
    ) -> HarnessDescriptor:
        descriptor = self.descriptors.get(harness_id)
        if descriptor is None:
            raise HarnessError(f"Unknown harness: {harness_id}")
        if not descriptor.available or not descriptor.ready:
            raise HarnessUnavailableError(
                descriptor.reason or f"Harness is not ready: {harness_id}"
            )
        if not descriptor.supports_capabilities(required_capabilities):
            missing = sorted(set(required_capabilities) - set(descriptor.capabilities))
            raise HarnessCapabilityError(
                f"Harness '{harness_id}' does not support: {', '.join(missing)}"
            )
        if harness_id not in self.runners:
            raise HarnessError(f"Harness has no runner: {harness_id}")
        return descriptor

    def run(
        self,
        harness_id: str,
        *,
        required_capabilities: set[str] | frozenset[str] = frozenset(),
        **kwargs: Any,
    ) -> Any:
        self.require(harness_id, required_capabilities=required_capabilities)
        return self.runners[harness_id](**kwargs)


def effective_harness(config: Any, role: str) -> str:
    """Return the explicit role harness, falling back to the legacy backend field."""
    selected = getattr(config, f"{role}_harness", None)
    return selected or getattr(config, "agent_backend", "native")


def route_id(provider: str | None, model: str | None) -> str:
    return f"{provider or 'unconfigured'}/{model or 'unconfigured'}"


def effective_provider(config: Any, role: str) -> str | None:
    """Resolve the provider identity independently from the execution harness."""
    if role == "supervisor":
        return (
            getattr(config, "supervisor_provider", None)
            or getattr(config, "worker_provider", None)
            or getattr(config, "worker_local_provider", None)
        )
    return getattr(config, "worker_provider", None) or getattr(config, "worker_local_provider", None)


def effective_model(config: Any, role: str) -> str | None:
    if role == "supervisor":
        return getattr(config, "supervisor_model", None) or getattr(config, "worker_model", None)
    return getattr(config, "worker_model", None)


def required_capabilities(config: Any, role: str) -> frozenset[str]:
    """Return the capabilities required by the current runtime contract."""
    required = {"tool_calls", "workspace_write"}
    if effective_harness(config, role) == "native":
        required.add("streaming")
    if getattr(config, "approval_policy", "never") != "never":
        required.add("approval_requests")
    return frozenset(required)


def event_metadata(
    config: Any,
    role: str,
    *,
    provider: str | None,
    model: str | None,
    session_id: str | None = None,
    parent_session_id: str | None = None,
    phase: str | None = None,
) -> dict[str, Any]:
    provider_id = provider or effective_provider(config, role)
    model_id = model or effective_model(config, role)
    metadata: dict[str, Any] = {
        "harness_id": effective_harness(config, role),
        "route_id": route_id(provider_id, model_id),
        "agent_kind": role,
        # Keep the short names for existing clients while exposing the
        # canonical identifiers to new clients and artifacts.
        "provider": provider_id or "unconfigured",
        "model": model_id or "unconfigured",
        "provider_id": provider_id or "unconfigured",
        "model_id": model_id or "unconfigured",
    }
    if session_id:
        metadata["session_id"] = session_id
    if parent_session_id:
        metadata["parent_session_id"] = parent_session_id
    if phase:
        metadata["phase"] = phase
    return metadata


def decorate_event(event: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    """Attach immutable harness identity to a streamed event."""
    return {**event, **metadata}


def normalized_external_events(
    *,
    raw_path: Path,
    output_path: Path,
    metadata: dict[str, Any],
    callback: Callable[[dict[str, Any]], None] | None = None,
    leading_event: str | None = None,
    trailing_event: str | None = None,
) -> None:
    """Convert process output into the same JSONL event envelope as native runs."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("", encoding="utf-8")
    sequence = 0

    def emit(payload: dict[str, Any]) -> None:
        nonlocal sequence
        sequence += 1
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **payload,
            **metadata,
            "sequence": sequence,
            "event_seq": sequence,
        }
        with output_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(event, ensure_ascii=True) + "\n")
        if callback is not None:
            callback(dict(event))

    if leading_event:
        emit({"type": leading_event})
    if raw_path.is_file():
        for line in raw_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = {"type": "harness.output", "text": text}
            if not isinstance(payload, dict):
                payload = {"type": "harness.output", "text": text}
            payload.setdefault("type", "harness.output")
            emit(payload)
    if trailing_event:
        emit({"type": trailing_event})


def available_harnesses(config: Any) -> list[dict[str, Any]]:
    native = HarnessDescriptor(
        harness_id="native",
        display_name="Agent Farm Native",
        capabilities=(
            "streaming",
            "tool_calls",
            "workspace_write",
            "approval_requests",
            "cancellation",
            "resumability",
        ),
        transports=("in_process", "http"),
        supports={
            "spawn": True,
            "fork": False,
            "continuation": True,
            "structured_output": True,
            "images": True,
            "remote_workspace": False,
        },
    )
    binary = str(getattr(config, "codex_binary", "codex"))
    binary_path = shutil.which(binary)
    if binary_path is None and Path(binary).is_file():
        binary_path = str(Path(binary).resolve())
    codex = HarnessDescriptor(
        harness_id="codex",
        display_name="Codex Compatibility",
        capabilities=("tool_calls", "workspace_write"),
        transports=("process", "http"),
        supports={
            "spawn": False,
            "fork": False,
            "continuation": False,
            "structured_output": True,
            "images": False,
            "remote_workspace": False,
        },
        available=True,
        ready=binary_path is not None,
        reason=None if binary_path else "Codex executable was not found.",
    )
    return [native.to_json(), codex.to_json()]


def named_subagent_providers(config: Any) -> list[dict[str, Any]]:
    """Expose harness-owned child providers without coupling them to model routes."""
    return [
        {
            "provider_id": f"{descriptor['harness_id']}.subagent",
            "harness_id": descriptor["harness_id"],
            "display_name": f"{descriptor['display_name']} Child Session",
            "available": descriptor["available"],
            "ready": descriptor["ready"],
            "supports": {
                "spawn": bool(descriptor["supports"].get("spawn")),
                "fork": bool(descriptor["supports"].get("fork")),
                "resume": bool(descriptor["supports"].get("continuation")),
            },
            **({"reason": descriptor["reason"]} if descriptor.get("reason") else {}),
        }
        for descriptor in available_harnesses(config)
    ]


def require_harness(
    config: Any,
    role: str,
    *,
    required_capabilities: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Validate a configured harness before a Supervisor or Worker starts traffic."""
    harness_id = effective_harness(config, role)
    descriptor = next(
        (item for item in available_harnesses(config) if item["harness_id"] == harness_id),
        None,
    )
    if descriptor is None:
        raise HarnessError(f"Unknown harness: {harness_id}")
    if not descriptor["available"] or not descriptor["ready"]:
        raise HarnessUnavailableError(
            descriptor.get("reason") or f"Harness is not ready: {harness_id}"
        )
    missing = sorted(set(required_capabilities) - set(descriptor["capabilities"]))
    if missing:
        raise HarnessCapabilityError(
            f"Harness '{harness_id}' does not support: {', '.join(missing)}"
        )
    return descriptor


def build_registry(
    config: Any,
    *,
    native_runner: HarnessRunner,
    codex_runner: HarnessRunner,
) -> HarnessRegistry:
    descriptors = {
        item["harness_id"]: HarnessDescriptor(
            harness_id=item["harness_id"],
            display_name=item["display_name"],
            capabilities=tuple(item["capabilities"]),
            transports=tuple(item["transports"]),
            supports=dict(item["supports"]),
            available=bool(item["available"]),
            ready=bool(item["ready"]),
            reason=item.get("reason"),
        )
        for item in available_harnesses(config)
    }
    return HarnessRegistry(
        descriptors=descriptors,
        runners={"native": native_runner, "codex": codex_runner},
    )
