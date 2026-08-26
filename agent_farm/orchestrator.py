from __future__ import annotations

import time
import json
import shlex
from pathlib import Path
from typing import Any, Callable

from .codex_worker import run_codex_worker as run_legacy_codex_worker
from .config import load_config, resolve_worker_profile
from .git_ops import (
    GitError,
    apply_patch,
    collect_changed_files,
    collect_patch,
    create_workspace_snapshot,
    create_worktree,
    find_repo_root,
    is_worktree_dirty,
    remove_worktree,
    resolve_ref,
)
from .harnesses import (
    build_registry,
    decorate_event,
    effective_harness,
    effective_provider,
    event_metadata,
    normalized_external_events,
    required_capabilities,
    route_id,
)
from .models import AgentFarmConfig, RunPaths, TaskStatus, TestResult
from .native_agent import run_native_worker
from .review import run_machine_review
from .sandbox import SandboxError, SandboxManager
from .specs import build_worker_prompt, make_task_id, read_task_spec
from .util import ensure_inside, read_json, write_json


class OrchestratorError(RuntimeError):
    pass


class OrchestratorCancelled(OrchestratorError):
    pass


def run_codex_worker(**kwargs):
    """Dispatch through the selected harness; name retained for API compatibility."""
    config = kwargs["config"]
    harness_id = effective_harness(config, "worker")
    callback = kwargs.pop("event_callback", None)
    paths = kwargs["paths"]
    model = kwargs.get("model") or config.worker_model
    metadata = event_metadata(
        config,
        "worker",
        provider=effective_provider(config, "worker"),
        model=model,
        session_id=paths.run_dir.name,
    )
    registry = build_registry(
        config,
        native_runner=run_native_worker,
        codex_runner=run_legacy_codex_worker,
    )
    if harness_id == "codex":
        registry.require(
            harness_id,
            required_capabilities=required_capabilities(config, "worker"),
        )
        kwargs.pop("approval_callback", None)
        kwargs.pop("cancel_check", None)
        kwargs.pop("model_attachments", None)
        kwargs.pop("usage_context", None)
        kwargs.pop("event_callback", None)
        raw_events = paths.worker_raw_events_file
        kwargs["events_file"] = raw_events
        try:
            result = registry.run(
                harness_id,
                required_capabilities=required_capabilities(config, "worker"),
                **kwargs,
            )
        finally:
            normalized_external_events(
                raw_path=raw_events,
                output_path=paths.worker_events_file,
                metadata={**metadata, "backend": "codex"},
                callback=callback,
                leading_event="agent.started",
                trailing_event="agent.completed" if 'result' in locals() and result.ok else "agent.failed",
            )
        return result
    if callback is not None:
        kwargs["event_callback"] = lambda event: callback(decorate_event(event, metadata))
    kwargs["event_metadata"] = metadata
    return registry.run(
        harness_id,
        required_capabilities=required_capabilities(config, "worker"),
        **kwargs,
    )


def _repo_relative(repo_root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return str(path.resolve())


def _merge_overrides(
    config: AgentFarmConfig,
    *,
    allowed_paths: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
    test_commands: list[str] | None = None,
    timeout_seconds: int | None = None,
    model: str | None = None,
    provider: str | None = None,
    secrets_env: str | None = None,
    worker_oss: bool | None = None,
    local_provider: str | None = None,
    codex_profile: str | None = None,
    codex_profile_v2: str | None = None,
) -> AgentFarmConfig:
    data = config.to_json()
    if allowed_paths:
        data["allowed_paths"] = list(allowed_paths)
    if forbidden_paths:
        data["forbidden_paths"] = list(config.forbidden_paths) + list(forbidden_paths)
    if test_commands:
        data["test_commands"] = list(test_commands)
    if timeout_seconds is not None:
        data["timeout_seconds"] = timeout_seconds
    if model is not None:
        data["worker_model"] = model
    if provider is not None:
        data["worker_provider"] = provider
    if secrets_env is not None:
        data["secrets_env"] = secrets_env
    if worker_oss is not None:
        data["worker_oss"] = worker_oss
    if local_provider is not None:
        data["worker_local_provider"] = local_provider
    if codex_profile is not None:
        data["worker_codex_profile"] = codex_profile
    if codex_profile_v2 is not None:
        data["worker_codex_profile_v2"] = codex_profile_v2
    return AgentFarmConfig.from_dict(data)


def _write_state(
    paths: RunPaths,
    *,
    status: TaskStatus,
    task_id: str,
    base_ref: str,
    base_commit: str,
    config: AgentFarmConfig,
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "task_id": task_id,
        "status": status.value,
        "base_ref": base_ref,
        "base_commit": base_commit,
        "repo_root": str(paths.repo_root),
        "worktree": str(paths.worktree),
        "run_dir": str(paths.run_dir),
        "config": config.to_json(),
        "role": "worker",
        "harness_id": effective_harness(config, "worker"),
        "route_id": route_id(effective_provider(config, "worker"), config.worker_model),
    }
    if extra:
        payload.update(extra)
    write_json(paths.result_file, payload)


def _run_tests(
    paths: RunPaths,
    commands: list[str],
    timeout_seconds: int,
    config: AgentFarmConfig,
    cancel_check: Callable[[], bool] | None = None,
) -> list[TestResult]:
    test_dir = paths.run_dir / "tests"
    test_dir.mkdir(parents=True, exist_ok=True)
    results: list[TestResult] = []
    sandbox = SandboxManager(
        backend=config.native_sandbox_backend,
        sandbox_mode=config.sandbox,
        memory_mb=config.native_sandbox_memory_mb,
        cpus=config.native_sandbox_cpus,
        pids=config.native_sandbox_pids,
        max_output_chars=config.native_max_output_chars,
        forbidden_paths=config.forbidden_paths,
    )
    for index, command in enumerate(commands, start=1):
        started = time.monotonic()
        argv = shlex.split(command, posix=True)
        try:
            result = sandbox.run(
                argv,
                worktree=paths.worktree,
                cwd=paths.worktree,
                timeout_seconds=timeout_seconds,
                cancel_check=cancel_check,
            )
            returncode = result.returncode
            stdout = result.stdout
            stderr = result.stderr
            timed_out = result.timed_out
            manifest = result.manifest
        except (SandboxError, OSError, ValueError) as exc:
            returncode = 126
            stdout = ""
            stderr = str(exc)
            timed_out = False
            manifest = {
                "schema_version": 1,
                "backend": "unavailable",
                "command": argv,
                "denied": True,
                "reason": str(exc),
            }
        duration = time.monotonic() - started
        log_file = test_dir / f"{index:02d}.log"
        log_file.write_text(
            f"$ {command}\n\n[sandbox]\n{json.dumps(manifest, indent=2)}"
            f"\n\n[stdout]\n{stdout}\n\n[stderr]\n{stderr}\n",
            encoding="utf-8",
        )
        write_json(test_dir / f"{index:02d}.capabilities.json", manifest)
        results.append(
            TestResult(
                command=command,
                returncode=returncode,
                log_file=str(log_file),
                duration_seconds=duration,
                timed_out=timed_out,
            )
        )
    return results


def prepare_dry_run(
    *,
    repo: Path,
    task_file: Path,
    config_path: Path | None = None,
    base_ref: str = "HEAD",
    allowed_paths: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
    test_commands: list[str] | None = None,
    timeout_seconds: int | None = None,
    model: str | None = None,
    provider: str | None = None,
    secrets_env: str | None = None,
    worker_oss: bool | None = None,
    local_provider: str | None = None,
    codex_profile: str | None = None,
    codex_profile_v2: str | None = None,
    profile: str | None = None,
) -> str:
    repo_root = find_repo_root(repo)
    base_config, _ = resolve_worker_profile(load_config(repo_root, config_path), profile)
    config = _merge_overrides(
        base_config,
        allowed_paths=allowed_paths,
        forbidden_paths=forbidden_paths,
        test_commands=test_commands,
        timeout_seconds=timeout_seconds,
        model=model,
        provider=provider,
        secrets_env=secrets_env,
        worker_oss=worker_oss,
        local_provider=local_provider,
        codex_profile=codex_profile,
        codex_profile_v2=codex_profile_v2,
    )
    base_commit = resolve_ref(repo_root, base_ref)
    task_spec = read_task_spec(task_file)
    return build_worker_prompt(task_spec=task_spec, base_commit=base_commit, config=config)


def run_task(
    *,
    repo: Path,
    task_file: Path,
    config_path: Path | None = None,
    base_ref: str = "HEAD",
    allowed_paths: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
    test_commands: list[str] | None = None,
    timeout_seconds: int | None = None,
    model: str | None = None,
    provider: str | None = None,
    secrets_env: str | None = None,
    worker_oss: bool | None = None,
    local_provider: str | None = None,
    codex_profile: str | None = None,
    codex_profile_v2: str | None = None,
    profile: str | None = None,
    task_id_override: str | None = None,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    approval_callback: Callable[[dict[str, Any]], str] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    attachment_context: str = "",
    model_attachments: list[dict[str, str]] | None = None,
    usage_context: dict[str, Any] | None = None,
    allow_no_changes: bool = False,
    snapshot_workspace: bool = True,
) -> dict[str, Any]:
    repo_root = find_repo_root(repo)
    base_config, selected_profile = resolve_worker_profile(
        load_config(repo_root, config_path),
        profile,
    )
    config = _merge_overrides(
        base_config,
        allowed_paths=allowed_paths,
        forbidden_paths=forbidden_paths,
        test_commands=test_commands,
        timeout_seconds=timeout_seconds,
        model=model,
        provider=provider,
        secrets_env=secrets_env,
        worker_oss=worker_oss,
        local_provider=local_provider,
        codex_profile=codex_profile,
        codex_profile_v2=codex_profile_v2,
    )
    base_commit = resolve_ref(repo_root, base_ref)
    if snapshot_workspace:
        base_commit = create_workspace_snapshot(
            repo_root,
            base_commit,
            include_paths=config.allowed_paths,
            forbidden_paths=config.forbidden_paths,
        )
    task_id = task_id_override or make_task_id(task_file)
    run_dir = (repo_root / config.runs_dir / task_id).resolve()
    worktree = (repo_root / config.worktrees_dir / task_id).resolve()
    ensure_inside(repo_root, run_dir)
    ensure_inside(repo_root, worktree)
    paths = RunPaths(repo_root=repo_root, run_dir=run_dir, worktree=worktree)

    run_dir.mkdir(parents=True, exist_ok=False)
    _write_state(
        paths,
        status=TaskStatus.CREATED,
        task_id=task_id,
        base_ref=base_ref,
        base_commit=base_commit,
        config=config,
    )

    task_spec = read_task_spec(task_file)
    prompt = build_worker_prompt(task_spec=task_spec, base_commit=base_commit, config=config)
    if attachment_context:
        prompt += f"\n\n## User Attachments\n\n{attachment_context.strip()}\n"
    paths.worker_prompt_file.write_text(prompt, encoding="utf-8")
    _write_state(
        paths,
        status=TaskStatus.SPEC_READY,
        task_id=task_id,
        base_ref=base_ref,
        base_commit=base_commit,
        config=config,
    )

    create_worktree(repo_root, worktree, base_commit)
    _write_state(
        paths,
        status=TaskStatus.WORKTREE_CREATED,
        task_id=task_id,
        base_ref=base_ref,
        base_commit=base_commit,
        config=config,
    )

    _write_state(
        paths,
        status=TaskStatus.WORKER_RUNNING,
        task_id=task_id,
        base_ref=base_ref,
        base_commit=base_commit,
        config=config,
    )
    worker_result = run_codex_worker(
        config=config,
        paths=paths,
        prompt=prompt,
        model=model,
        timeout_seconds=timeout_seconds,
        event_callback=event_callback,
        approval_callback=approval_callback,
        cancel_check=cancel_check,
        model_attachments=model_attachments,
        usage_context=usage_context,
    )
    if worker_result.returncode == 130:
        _write_state(
            paths,
            status=TaskStatus.CANCELLED,
            task_id=task_id,
            base_ref=base_ref,
            base_commit=base_commit,
            config=config,
            extra={"worker": {"cancelled": True}},
        )
        raise OrchestratorCancelled("Worker execution was cancelled.")
    _write_state(
        paths,
        status=TaskStatus.WORKER_FINISHED,
        task_id=task_id,
        base_ref=base_ref,
        base_commit=base_commit,
        config=config,
        extra={
            "worker": {
                "backend": effective_harness(config, "worker"),
                "harness_id": effective_harness(config, "worker"),
                "route_id": route_id(effective_provider(config, "worker"), config.worker_model),
                "provider_id": effective_provider(config, "worker") or "unconfigured",
                "model_id": config.worker_model or "unconfigured",
                "profile": selected_profile,
                "returncode": worker_result.returncode,
                "timed_out": worker_result.timed_out,
                "events_file": str(paths.worker_events_file),
                "raw_events_file": (
                    str(paths.worker_raw_events_file)
                    if paths.worker_raw_events_file.is_file()
                    else None
                ),
                "stderr_file": str(paths.worker_stderr_file),
                "final_file": str(paths.worker_final_file),
            }
        },
    )

    _write_state(
        paths,
        status=TaskStatus.TESTING,
        task_id=task_id,
        base_ref=base_ref,
        base_commit=base_commit,
        config=config,
    )
    test_results = _run_tests(
        paths,
        config.test_commands,
        config.test_timeout_seconds,
        config,
        cancel_check,
    )
    changed_files = collect_changed_files(
        worktree,
        include_ignored_paths=config.allowed_paths,
    )
    patch = collect_patch(worktree, include_ignored_paths=config.allowed_paths)
    # Git binary patches require LF-preserving payload lines on Windows.
    paths.patch_file.write_text(patch, encoding="utf-8", newline="")
    worker_failure = None
    if worker_result.timed_out:
        worker_failure = "Worker agent timed out."
    elif worker_result.returncode != 0:
        worker_failure = f"Worker agent exited with code {worker_result.returncode}."
    machine_review = run_machine_review(
        config,
        changed_files,
        patch,
        test_results,
        worker_ok=worker_failure is None,
        worker_failure_message=worker_failure,
        allow_no_changes=allow_no_changes,
    )

    final_status = (
        TaskStatus.SUPERVISOR_REVIEW_PENDING
        if machine_review.passed
        else TaskStatus.REVISION_REQUESTED
    )
    payload = {
        "worker": {
            "harness_id": effective_harness(config, "worker"),
            "route_id": route_id(effective_provider(config, "worker"), config.worker_model),
            "provider_id": effective_provider(config, "worker") or "unconfigured",
            "model_id": config.worker_model or "unconfigured",
            "profile": selected_profile,
            "returncode": worker_result.returncode,
            "timed_out": worker_result.timed_out,
            "events_file": str(paths.worker_events_file),
            "raw_events_file": (
                str(paths.worker_raw_events_file)
                if paths.worker_raw_events_file.is_file()
                else None
            ),
            "stderr_file": str(paths.worker_stderr_file),
            "final_file": str(paths.worker_final_file),
        },
        "changed_files": [item.to_json() for item in changed_files],
        "patch_file": str(paths.patch_file),
        "tests": [item.to_json() for item in test_results],
        "machine_review": machine_review.to_json(),
        "session_id": paths.run_dir.name,
        "stop_reason": "completed" if machine_review.passed else "blocked",
    }
    _write_state(
        paths,
        status=final_status,
        task_id=task_id,
        base_ref=base_ref,
        base_commit=base_commit,
        config=config,
        extra=payload,
    )
    return read_json(paths.result_file)


def review_run(run_dir: Path) -> dict[str, Any]:
    result_file = run_dir / "result.json"
    if not result_file.exists():
        raise OrchestratorError(f"No result.json found in {run_dir}")
    return read_json(result_file)


def merge_run(run_dir: Path, *, repo: Path, yes: bool, allow_dirty: bool = False) -> dict[str, Any]:
    if not yes:
        raise OrchestratorError("Refusing to merge without --yes.")

    repo_root = find_repo_root(repo)
    result = review_run(run_dir)
    review = result.get("machine_review", {})
    if review.get("status") != "passed":
        raise OrchestratorError("Machine review did not pass; refusing to merge.")

    if is_worktree_dirty(repo_root) and not allow_dirty:
        raise OrchestratorError("Supervisor workspace is dirty; pass --allow-dirty to override.")

    patch_file = Path(result["patch_file"])
    apply_patch(repo_root, patch_file)
    result["status"] = TaskStatus.MERGED.value
    result["merge"] = {"applied_patch": str(patch_file)}
    write_json(run_dir / "result.json", result)
    return result


def cleanup_run(run_dir: Path, *, repo: Path, force: bool = False) -> None:
    repo_root = find_repo_root(repo)
    result = review_run(run_dir)
    worktree = Path(result["worktree"])
    try:
        remove_worktree(repo_root, worktree, force=force)
    except GitError as exc:
        raise OrchestratorError(str(exc)) from exc
