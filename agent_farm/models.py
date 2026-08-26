from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class TaskStatus(str, Enum):
    CREATED = "CREATED"
    SPEC_READY = "SPEC_READY"
    WORKTREE_CREATED = "WORKTREE_CREATED"
    WORKER_RUNNING = "WORKER_RUNNING"
    WORKER_FINISHED = "WORKER_FINISHED"
    TESTING = "TESTING"
    MACHINE_REVIEW_PENDING = "MACHINE_REVIEW_PENDING"
    MACHINE_REVIEW_PASSED = "MACHINE_REVIEW_PASSED"
    SUPERVISOR_REVIEW_PENDING = "SUPERVISOR_REVIEW_PENDING"
    SUPERVISOR_APPROVED = "SUPERVISOR_APPROVED"
    COMPLETED = "COMPLETED"
    REVIEW_PENDING = "REVIEW_PENDING"
    APPROVED = "APPROVED"
    REVISION_REQUESTED = "REVISION_REQUESTED"
    REJECTED = "REJECTED"
    CHECKPOINT_CREATED = "CHECKPOINT_CREATED"
    MERGED = "MERGED"
    ROLLBACK_REQUESTED = "ROLLBACK_REQUESTED"
    ROLLED_BACK = "ROLLED_BACK"
    ABANDONED = "ABANDONED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class CommandResult:
    args: list[str] | str
    cwd: str
    returncode: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


@dataclass(frozen=True)
class ChangedFile:
    status: str
    path: str
    old_path: str | None = None

    @property
    def review_path(self) -> str:
        return self.path

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {"status": self.status, "path": self.path}
        if self.old_path:
            data["old_path"] = self.old_path
        return data


@dataclass(frozen=True)
class TestResult:
    command: str
    returncode: int
    log_file: str
    duration_seconds: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def to_json(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "returncode": self.returncode,
            "log_file": self.log_file,
            "duration_seconds": round(self.duration_seconds, 3),
            "timed_out": self.timed_out,
        }


@dataclass(frozen=True)
class ReviewFinding:
    severity: str
    code: str
    message: str
    path: str | None = None

    def to_json(self) -> dict[str, str]:
        data = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }
        if self.path:
            data["path"] = self.path
        return data


@dataclass(frozen=True)
class MachineReview:
    status: str
    findings: list[ReviewFinding] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "findings": [finding.to_json() for finding in self.findings],
        }


@dataclass(frozen=True)
class AgentFarmConfig:
    agent_backend: str = "native"
    # Role-specific harnesses are additive to the legacy agent_backend field.
    # None means "use agent_backend" for old configurations.
    supervisor_harness: str | None = None
    worker_harness: str | None = None
    codex_binary: str = "codex"
    supervisor_model: str | None = None
    supervisor_provider: str | None = None
    supervisor_reasoning_mode: str | None = None
    supervisor_reasoning_effort: str | None = None
    supervisor_codex_profile: str | None = None
    supervisor_timeout_seconds: int = 900
    auto_supervisor_review: bool = True
    worker_model: str | None = None
    worker_provider: str | None = None
    worker_reasoning_mode: str | None = None
    worker_reasoning_effort: str | None = None
    worker_oss: bool = False
    worker_local_provider: str | None = None
    worker_codex_profile: str | None = None
    worker_codex_profile_v2: str | None = None
    secrets_env: str | None = ".agent-farm/secrets.env"
    model_providers: dict[str, dict[str, Any]] = field(default_factory=dict)
    model_price_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    worker_budget_usd: float | None = None
    farm_budget_usd: float | None = None
    monthly_budget_usd: float | None = None
    budget_policy: str = "warn"
    budget_warning_ratio: float = 0.8
    provider_failure_threshold: int = 3
    provider_cooldown_seconds: int = 60
    max_worker_escalations: int = 1
    codex_config_overrides: dict[str, Any] = field(default_factory=dict)
    worker_profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    default_worker_profile: str | None = None
    max_parallel_workers: int = 4
    sandbox: str = "workspace-write"
    approval_policy: str = "never"
    runs_dir: str = ".agent-farm/runs"
    farms_dir: str = ".agent-farm/farms"
    worktrees_dir: str = ".agent-farm/worktrees"
    timeout_seconds: int = 1800
    native_max_turns: int = 24
    native_command_timeout_seconds: int = 180
    native_max_output_chars: int = 24_000
    native_sandbox_backend: str = "auto"
    native_sandbox_memory_mb: int = 1024
    native_sandbox_cpus: float = 2.0
    native_sandbox_pids: int = 256
    test_timeout_seconds: int = 600
    artifact_retention_days: int = 30
    max_runtime_backups: int = 5
    max_diagnostic_bundles: int = 10
    max_diff_lines: int = 800
    max_changed_files: int = 25
    allowed_paths: list[str] = field(default_factory=list)
    forbidden_paths: list[str] = field(default_factory=list)
    test_commands: list[str] = field(default_factory=list)
    allow_lockfiles: bool = False
    codex_json: bool = True
    ephemeral: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentFarmConfig":
        known = {field.name for field in cls.__dataclass_fields__.values()}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"Unknown config keys: {', '.join(unknown)}")
        return cls(**data)

    def to_json(self) -> dict[str, Any]:
        return {
            "agent_backend": self.agent_backend,
            "supervisor_harness": self.supervisor_harness,
            "worker_harness": self.worker_harness,
            "codex_binary": self.codex_binary,
            "supervisor_model": self.supervisor_model,
            "supervisor_provider": self.supervisor_provider,
            "supervisor_reasoning_mode": self.supervisor_reasoning_mode,
            "supervisor_reasoning_effort": self.supervisor_reasoning_effort,
            "supervisor_codex_profile": self.supervisor_codex_profile,
            "supervisor_timeout_seconds": self.supervisor_timeout_seconds,
            "auto_supervisor_review": self.auto_supervisor_review,
            "worker_model": self.worker_model,
            "worker_provider": self.worker_provider,
            "worker_reasoning_mode": self.worker_reasoning_mode,
            "worker_reasoning_effort": self.worker_reasoning_effort,
            "worker_oss": self.worker_oss,
            "worker_local_provider": self.worker_local_provider,
            "worker_codex_profile": self.worker_codex_profile,
            "worker_codex_profile_v2": self.worker_codex_profile_v2,
            "secrets_env": self.secrets_env,
            "model_providers": self.model_providers,
            "model_price_overrides": self.model_price_overrides,
            "worker_budget_usd": self.worker_budget_usd,
            "farm_budget_usd": self.farm_budget_usd,
            "monthly_budget_usd": self.monthly_budget_usd,
            "budget_policy": self.budget_policy,
            "budget_warning_ratio": self.budget_warning_ratio,
            "provider_failure_threshold": self.provider_failure_threshold,
            "provider_cooldown_seconds": self.provider_cooldown_seconds,
            "max_worker_escalations": self.max_worker_escalations,
            "codex_config_overrides": self.codex_config_overrides,
            "worker_profiles": self.worker_profiles,
            "default_worker_profile": self.default_worker_profile,
            "max_parallel_workers": self.max_parallel_workers,
            "sandbox": self.sandbox,
            "approval_policy": self.approval_policy,
            "runs_dir": self.runs_dir,
            "farms_dir": self.farms_dir,
            "worktrees_dir": self.worktrees_dir,
            "timeout_seconds": self.timeout_seconds,
            "native_max_turns": self.native_max_turns,
            "native_command_timeout_seconds": self.native_command_timeout_seconds,
            "native_max_output_chars": self.native_max_output_chars,
            "native_sandbox_backend": self.native_sandbox_backend,
            "native_sandbox_memory_mb": self.native_sandbox_memory_mb,
            "native_sandbox_cpus": self.native_sandbox_cpus,
            "native_sandbox_pids": self.native_sandbox_pids,
            "test_timeout_seconds": self.test_timeout_seconds,
            "artifact_retention_days": self.artifact_retention_days,
            "max_runtime_backups": self.max_runtime_backups,
            "max_diagnostic_bundles": self.max_diagnostic_bundles,
            "max_diff_lines": self.max_diff_lines,
            "max_changed_files": self.max_changed_files,
            "allowed_paths": list(self.allowed_paths),
            "forbidden_paths": list(self.forbidden_paths),
            "test_commands": list(self.test_commands),
            "allow_lockfiles": self.allow_lockfiles,
            "codex_json": self.codex_json,
            "ephemeral": self.ephemeral,
        }


@dataclass(frozen=True)
class RunPaths:
    repo_root: Path
    run_dir: Path
    worktree: Path

    @property
    def result_file(self) -> Path:
        return self.run_dir / "result.json"

    @property
    def patch_file(self) -> Path:
        return self.run_dir / "patch.diff"

    @property
    def worker_prompt_file(self) -> Path:
        return self.run_dir / "worker-prompt.md"

    @property
    def worker_events_file(self) -> Path:
        return self.run_dir / "worker-events.jsonl"

    @property
    def worker_raw_events_file(self) -> Path:
        return self.run_dir / "codex-events.raw.jsonl"

    @property
    def worker_stderr_file(self) -> Path:
        return self.run_dir / "worker-stderr.log"

    @property
    def worker_final_file(self) -> Path:
        return self.run_dir / "worker-final.md"
