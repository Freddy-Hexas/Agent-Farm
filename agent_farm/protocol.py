from __future__ import annotations

import uuid
from typing import Any, NotRequired, TypedDict


PROTOCOL_VERSION = 1
SUPPORTED_PROTOCOL_VERSIONS = (PROTOCOL_VERSION,)
SERVER_CAPABILITIES = frozenset(
    {
        "approvals.v1",
        "attachments.v1",
        "cancellation.v1",
        "durable-jobs.v1",
        "model-deltas.v1",
        "reconnect-cursor.v1",
        "sessions.v1",
        "subagents.v1",
        "tasks.v1",
        "typed-messages.v1",
        "harness-registry.v1",
    }
)


class ProtocolNegotiationError(RuntimeError):
    pass


class ThreadMessage(TypedDict):
    schema_version: int
    thread_id: str
    title: str
    status: str
    turns: list["TurnMessage"]


class TurnMessage(TypedDict):
    schema_version: int
    turn_id: str
    status: str
    items: list["ItemMessage"]


class ItemMessage(TypedDict):
    schema_version: int
    item_id: str
    type: str
    status: str
    payload: dict[str, Any]


class WorkerMessage(TypedDict):
    schema_version: int
    worker_id: str
    status: str
    provider: NotRequired[str]
    model: NotRequired[str]
    provider_id: NotRequired[str]
    model_id: NotRequired[str]
    harness_id: NotRequired[str]
    route_id: NotRequired[str]
    session_id: NotRequired[str]
    parent_session_id: NotRequired[str]
    stop_reason: NotRequired[str]


class ToolMessage(TypedDict):
    schema_version: int
    call_id: str
    name: str
    status: str
    arguments: dict[str, Any]
    output: NotRequired[dict[str, Any]]


class DiffMessage(TypedDict):
    schema_version: int
    worker_id: str
    files: list[dict[str, Any]]
    unified_diff: str
    truncated: bool


class ApprovalMessage(TypedDict):
    schema_version: int
    approval_id: str
    job_kind: str
    job_id: str
    kind: str
    scope: str
    status: str
    decision: NotRequired[str | None]


class UsageMessage(TypedDict):
    schema_version: int
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: NotRequired[float | None]


def _object_schema(
    name: str,
    required: list[str],
    properties: dict[str, Any],
) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://agentfarm.local/protocol/v1/{name}.schema.json",
        "title": f"Agent Farm {name.title()} Message",
        "type": "object",
        "required": ["schema_version", *required],
        "properties": {
            "schema_version": {"type": "integer", "const": PROTOCOL_VERSION},
            **properties,
        },
        "additionalProperties": True,
    }


MESSAGE_SCHEMAS: dict[str, dict[str, Any]] = {
    "thread": _object_schema(
        "thread",
        ["thread_id", "title", "status", "turns"],
        {
            "thread_id": {"type": "string"},
            "title": {"type": "string"},
            "status": {"type": "string"},
            "turns": {"type": "array", "items": {"$ref": "turn.schema.json"}},
        },
    ),
    "turn": _object_schema(
        "turn",
        ["turn_id", "status", "items"],
        {
            "turn_id": {"type": "string"},
            "status": {"type": "string"},
            "items": {"type": "array", "items": {"$ref": "item.schema.json"}},
        },
    ),
    "item": _object_schema(
        "item",
        ["item_id", "type", "status", "payload"],
        {
            "item_id": {"type": "string"},
            "type": {"type": "string"},
            "status": {"type": "string"},
            "payload": {"type": "object"},
        },
    ),
    "worker": _object_schema(
        "worker",
        ["worker_id", "status"],
        {
            "worker_id": {"type": "string"},
            "status": {"type": "string"},
            "provider": {"type": "string"},
            "model": {"type": "string"},
            "provider_id": {"type": "string"},
            "model_id": {"type": "string"},
            "harness_id": {"type": "string"},
            "route_id": {"type": "string"},
            "session_id": {"type": "string"},
            "parent_session_id": {"type": "string"},
            "stop_reason": {"type": "string"},
        },
    ),
    "tool": _object_schema(
        "tool",
        ["call_id", "name", "status", "arguments"],
        {
            "call_id": {"type": "string"},
            "name": {"type": "string"},
            "status": {"type": "string"},
            "arguments": {"type": "object"},
            "output": {"type": "object"},
        },
    ),
    "diff": _object_schema(
        "diff",
        ["worker_id", "files", "unified_diff", "truncated"],
        {
            "worker_id": {"type": "string"},
            "files": {"type": "array", "items": {"type": "object"}},
            "unified_diff": {"type": "string"},
            "truncated": {"type": "boolean"},
        },
    ),
    "approval": _object_schema(
        "approval",
        ["approval_id", "job_kind", "job_id", "kind", "scope", "status"],
        {
            "approval_id": {"type": "string"},
            "job_kind": {"type": "string", "enum": ["plan", "farm"]},
            "job_id": {"type": "string"},
            "kind": {"type": "string", "enum": ["command", "file_write", "network"]},
            "scope": {"type": "string"},
            "status": {"type": "string", "enum": ["pending", "resolved"]},
            "decision": {
                "type": ["string", "null"],
                "enum": ["allow_once", "allow_session", "deny", "cancel", None],
            },
        },
    ),
    "usage": _object_schema(
        "usage",
        ["provider", "model", "input_tokens", "output_tokens", "total_tokens"],
        {
            "provider": {"type": "string"},
            "model": {"type": "string"},
            "input_tokens": {"type": "integer", "minimum": 0},
            "output_tokens": {"type": "integer", "minimum": 0},
            "total_tokens": {"type": "integer", "minimum": 0},
            "estimated_cost_usd": {"type": ["number", "null"], "minimum": 0},
        },
    ),
}


def protocol_descriptor(*, include_schemas: bool = False) -> dict[str, Any]:
    descriptor: dict[str, Any] = {
        "protocol_version": PROTOCOL_VERSION,
        "supported_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
        "capabilities": sorted(SERVER_CAPABILITIES),
        "message_schemas": {
            name: {"version": PROTOCOL_VERSION, "id": schema["$id"]}
            for name, schema in MESSAGE_SCHEMAS.items()
        },
    }
    if include_schemas:
        descriptor["schemas"] = MESSAGE_SCHEMAS
    return descriptor


def negotiate_protocol(payload: dict[str, Any]) -> dict[str, Any]:
    known = {
        "client_name",
        "client_version",
        "protocol_versions",
        "capabilities",
        "required_capabilities",
    }
    unknown = sorted(set(payload) - known)
    if unknown:
        raise ValueError("Unknown initialization fields: " + ", ".join(unknown))
    versions = payload.get("protocol_versions")
    capabilities = payload.get("capabilities", [])
    required = payload.get("required_capabilities", [])
    if not isinstance(versions, list) or any(type(value) is not int for value in versions):
        raise ValueError("protocol_versions must be an array of integers.")
    if not isinstance(capabilities, list) or any(not isinstance(value, str) for value in capabilities):
        raise ValueError("capabilities must be an array of strings.")
    if not isinstance(required, list) or any(not isinstance(value, str) for value in required):
        raise ValueError("required_capabilities must be an array of strings.")
    selected = next(
        (version for version in SUPPORTED_PROTOCOL_VERSIONS if version in versions),
        None,
    )
    if selected is None:
        raise ProtocolNegotiationError(
            "No compatible Agent Farm protocol version is available."
        )
    missing = sorted(set(required) - SERVER_CAPABILITIES)
    if missing:
        raise ProtocolNegotiationError(
            "Required runtime capabilities are unavailable: " + ", ".join(missing)
        )
    return {
        "session_id": uuid.uuid4().hex,
        "protocol_version": selected,
        "server_capabilities": sorted(SERVER_CAPABILITIES),
        "enabled_capabilities": sorted(set(capabilities) & SERVER_CAPABILITIES),
        "message_schemas": protocol_descriptor()["message_schemas"],
    }
