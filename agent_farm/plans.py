from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from .specs import slugify

WORKER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
DECISIONS = {"approve_merge", "request_revision", "reject", "hold_for_user", "rollback"}
RISK_LEVELS = {"low", "medium", "high"}
TASK_COMPLEXITIES = {"simple", "standard", "complex"}


def _string_list(data: dict[str, Any], key: str) -> list[str]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be an array of strings")
    return list(value)


@dataclass(frozen=True)
class DeliverableSpec:
    path: str
    instructions: str

    @classmethod
    def from_dict(cls, data: Any) -> "DeliverableSpec":
        if not isinstance(data, dict):
            raise ValueError("deliverable must be a JSON object or null")
        unknown = sorted(set(data) - {"path", "instructions"})
        if unknown:
            raise ValueError("Unknown deliverable keys: " + ", ".join(unknown))
        raw_path = data.get("path")
        instructions = data.get("instructions")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("deliverable.path must be a non-empty string")
        normalized = raw_path.strip().replace("\\", "/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts or normalized.startswith("/"):
            raise ValueError("deliverable.path must stay inside the repository")
        if normalized == "." or normalized.startswith(".agent-farm/"):
            raise ValueError("deliverable.path must name a user-visible repository file")
        if not isinstance(instructions, str) or not instructions.strip():
            raise ValueError("deliverable.instructions must be a non-empty string")
        return cls(path=normalized, instructions=instructions.strip())

    def to_json(self) -> dict[str, str]:
        return {"path": self.path, "instructions": self.instructions}


@dataclass(frozen=True)
class WorkerPlanItem:
    worker_id: str
    role: str
    profile: str
    goal: str
    allowed_paths: list[str] = field(default_factory=list)
    forbidden_paths: list[str] = field(default_factory=list)
    test_commands: list[str] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)
    context: str = ""
    complexity: str = "standard"
    attachments: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, index: int) -> "WorkerPlanItem":
        if not isinstance(data, dict):
            raise ValueError(f"workers[{index}] must be a JSON object")
        known = {
            "id",
            "role",
            "profile",
            "goal",
            "allowed_paths",
            "forbidden_paths",
            "test_commands",
            "acceptance",
            "context",
            "complexity",
            "attachments",
            "depends_on",
        }
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"Unknown worker keys at index {index}: {', '.join(unknown)}")

        role = data.get("role")
        profile = data.get("profile")
        goal = data.get("goal")
        if not isinstance(role, str) or not role.strip():
            raise ValueError(f"workers[{index}].role must be a non-empty string")
        if not isinstance(profile, str) or not profile.strip():
            raise ValueError(f"workers[{index}].profile must be a non-empty string")
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError(f"workers[{index}].goal must be a non-empty string")

        raw_id = data.get("id")
        worker_id = raw_id if isinstance(raw_id, str) and raw_id else f"{slugify(role)}-{index + 1}"
        worker_id = worker_id.lower()
        if not WORKER_ID_RE.fullmatch(worker_id):
            raise ValueError(
                f"workers[{index}].id must contain only lowercase letters, numbers, '.', '_' or '-'"
            )
        context = data.get("context", "")
        if not isinstance(context, str):
            raise ValueError(f"workers[{index}].context must be a string")
        complexity = data.get("complexity", "standard")
        if complexity not in TASK_COMPLEXITIES:
            raise ValueError(
                f"workers[{index}].complexity must be simple, standard, or complex"
            )

        return cls(
            worker_id=worker_id,
            role=role.strip(),
            profile=profile.strip(),
            goal=goal.strip(),
            allowed_paths=_string_list(data, "allowed_paths"),
            forbidden_paths=_string_list(data, "forbidden_paths"),
            test_commands=_string_list(data, "test_commands"),
            acceptance=_string_list(data, "acceptance"),
            context=context.strip(),
            complexity=complexity,
            attachments=_string_list(data, "attachments"),
            depends_on=_string_list(data, "depends_on"),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.worker_id,
            "role": self.role,
            "profile": self.profile,
            "goal": self.goal,
            "allowed_paths": list(self.allowed_paths),
            "forbidden_paths": list(self.forbidden_paths),
            "test_commands": list(self.test_commands),
            "acceptance": list(self.acceptance),
            "context": self.context,
            "complexity": self.complexity,
            "attachments": list(self.attachments),
            "depends_on": list(self.depends_on),
        }

    def to_task_spec(self) -> str:
        acceptance = "\n".join(f"- {item}" for item in self.acceptance) or "- Complete the goal safely."
        context = self.context or "No additional context was supplied by the supervisor."
        return f"""# Worker Task: {self.role}

## Goal

{self.goal}

## Context From Supervisor

{context}

## Acceptance

{acceptance}

## Role And Cost Boundary

- Worker id: `{self.worker_id}`
- Worker role: `{self.role}`
- Model profile: `{self.profile}`
- Task complexity: `{self.complexity}`
- Assigned attachment IDs: `{', '.join(self.attachments) or 'none'}`
- Runs after: `{', '.join(self.depends_on) or 'no dependencies'}`
- The expensive supervisor retains planning, final review, and merge authority.
"""


@dataclass(frozen=True)
class WorkerPlan:
    task_id: str
    workers: list[WorkerPlanItem]
    schema_version: int = 1
    base_ref: str = "HEAD"
    max_parallel: int | None = None
    deliverable: DeliverableSpec | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkerPlan":
        if not isinstance(data, dict):
            raise ValueError("Worker plan must be a JSON object")
        known = {
            "schema_version",
            "task_id",
            "workers",
            "base_ref",
            "max_parallel",
            "deliverable",
        }
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"Unknown worker plan keys: {', '.join(unknown)}")

        schema_version = data.get("schema_version", 1)
        if schema_version != 1:
            raise ValueError(f"Unsupported worker plan schema_version: {schema_version}")

        raw_workers = data.get("workers")
        if not isinstance(raw_workers, list) or not raw_workers:
            raise ValueError("Worker plan must contain at least one worker")
        workers = [
            WorkerPlanItem.from_dict(item, index=index)
            for index, item in enumerate(raw_workers)
        ]
        ids = [item.worker_id for item in workers]
        if len(ids) != len(set(ids)):
            raise ValueError("Worker ids must be unique")
        worker_ids = set(ids)
        for worker in workers:
            unknown_dependencies = sorted(set(worker.depends_on) - worker_ids)
            if unknown_dependencies:
                raise ValueError(
                    f"Worker '{worker.worker_id}' has unknown dependencies: "
                    + ", ".join(unknown_dependencies)
                )
            if worker.worker_id in worker.depends_on:
                raise ValueError(f"Worker '{worker.worker_id}' cannot depend on itself")
            if len(worker.depends_on) != len(set(worker.depends_on)):
                raise ValueError(f"Worker '{worker.worker_id}' has duplicate dependencies")
        remaining = {worker.worker_id: set(worker.depends_on) for worker in workers}
        resolved: set[str] = set()
        while remaining:
            ready = sorted(
                worker_id
                for worker_id, dependencies in remaining.items()
                if dependencies <= resolved
            )
            if not ready:
                cycle = ", ".join(sorted(remaining))
                raise ValueError(f"Worker dependency graph contains a cycle: {cycle}")
            resolved.update(ready)
            for worker_id in ready:
                del remaining[worker_id]

        raw_task_id = data.get("task_id", "task")
        if not isinstance(raw_task_id, str):
            raise ValueError("task_id must be a string")
        task_id = slugify(raw_task_id, fallback="task")
        base_ref = data.get("base_ref", "HEAD")
        if not isinstance(base_ref, str) or not base_ref:
            raise ValueError("base_ref must be a non-empty string")
        max_parallel = data.get("max_parallel")
        if max_parallel is not None and (type(max_parallel) is not int or max_parallel < 1):
            raise ValueError("max_parallel must be a positive integer")
        raw_deliverable = data.get("deliverable")
        deliverable = (
            DeliverableSpec.from_dict(raw_deliverable)
            if raw_deliverable is not None
            else None
        )
        return cls(
            task_id=task_id,
            workers=workers,
            schema_version=schema_version,
            base_ref=base_ref,
            max_parallel=max_parallel,
            deliverable=deliverable,
        )

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "base_ref": self.base_ref,
            "workers": [worker.to_json() for worker in self.workers],
        }
        if self.max_parallel is not None:
            data["max_parallel"] = self.max_parallel
        if self.deliverable is not None:
            data["deliverable"] = self.deliverable.to_json()
        return data


@dataclass(frozen=True)
class SupervisorDecision:
    decision: str
    task_id: str
    reason: str
    risk_level: str
    approved_worker: str | None = None
    rollback_required: bool = True
    schema_version: int = 1

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SupervisorDecision":
        if not isinstance(data, dict):
            raise ValueError("Supervisor decision must be a JSON object")
        known = {
            "schema_version",
            "decision",
            "task_id",
            "reason",
            "risk_level",
            "approved_worker",
            "rollback_required",
        }
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"Unknown supervisor decision keys: {', '.join(unknown)}")
        schema_version = data.get("schema_version", 1)
        if schema_version != 1:
            raise ValueError(f"Unsupported supervisor decision schema_version: {schema_version}")
        decision = data.get("decision")
        task_id = data.get("task_id")
        reason = data.get("reason")
        risk_level = data.get("risk_level")
        approved_worker = data.get("approved_worker")
        rollback_required = data.get("rollback_required", True)
        if decision not in DECISIONS:
            raise ValueError(f"Invalid supervisor decision: {decision}")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task_id must be a non-empty string")
        if not isinstance(reason, str) or not reason:
            raise ValueError("reason must be a non-empty string")
        if risk_level not in RISK_LEVELS:
            raise ValueError(f"Invalid risk_level: {risk_level}")
        if approved_worker is not None and not isinstance(approved_worker, str):
            raise ValueError("approved_worker must be a string or null")
        if decision == "approve_merge" and not approved_worker:
            raise ValueError("approve_merge requires approved_worker")
        if decision != "approve_merge" and approved_worker is not None:
            raise ValueError("approved_worker must be null unless decision is approve_merge")
        if not isinstance(rollback_required, bool):
            raise ValueError("rollback_required must be a boolean")
        return cls(
            decision=decision,
            task_id=task_id,
            reason=reason,
            risk_level=risk_level,
            approved_worker=approved_worker,
            rollback_required=rollback_required,
            schema_version=schema_version,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "decision": self.decision,
            "task_id": self.task_id,
            "approved_worker": self.approved_worker,
            "risk_level": self.risk_level,
            "reason": self.reason,
            "rollback_required": self.rollback_required,
        }


def read_worker_plan(path: Path) -> WorkerPlan:
    return WorkerPlan.from_dict(json.loads(path.read_text(encoding="utf-8-sig")))


def read_supervisor_decision(path: Path) -> SupervisorDecision:
    return SupervisorDecision.from_dict(json.loads(path.read_text(encoding="utf-8-sig")))
