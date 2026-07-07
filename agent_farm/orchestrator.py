from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .codex_worker import run_codex_worker
from .config import load_config
from .git_ops import (
    GitError,
    apply_patch,
    collect_changed_files,
    collect_patch,
    create_worktree,
    find_repo_root,
    is_worktree_dirty,
    remove_worktree,
    resolve_ref,
)
from .models import AgentFarmConfig, RunPaths, TaskStatus, TestResult
from .review import run_machine_review
from .specs import build_worker_prompt, make_task_id, read_task_spec
from .util import ensure_inside, read_json, run_command, write_json


class OrchestratorError(RuntimeError):
    pass


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
    }
    if extra:
        payload.update(extra)
    write_json(paths.result_file, payload)


def _run_tests(paths: RunPaths, commands: list[str], timeout_seconds: int) -> list[TestResult]:
    test_dir = paths.run_dir / "tests"
    test_dir.mkdir(parents=True, exist_ok=True)
    results: list[TestResult] = []
    for index, command in enumerate(commands, start=1):
        started = time.monotonic()
        result = run_command(
            command,
            paths.worktree,
            timeout_seconds=timeout_seconds,
            shell=True,
        )
        duration = time.monotonic() - started
        log_file = test_dir / f"{index:02d}.log"
        log_file.write_text(
            f"$ {command}\n\n[stdout]\n{result.stdout}\n\n[stderr]\n{result.stderr}\n",
            encoding="utf-8",
        )
        results.append(
            TestResult(
                command=command,
                returncode=result.returncode,
                log_file=str(log_file),
                duration_seconds=duration,
                timed_out=result.timed_out,
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
) -> str:
    repo_root = find_repo_root(repo)
    config = _merge_overrides(
        load_config(repo_root, config_path),
        allowed_paths=allowed_paths,
        forbidden_paths=forbidden_paths,
        test_commands=test_commands,
        timeout_seconds=timeout_seconds,
        model=model,
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
) -> dict[str, Any]:
    repo_root = find_repo_root(repo)
    config = _merge_overrides(
        load_config(repo_root, config_path),
        allowed_paths=allowed_paths,
        forbidden_paths=forbidden_paths,
        test_commands=test_commands,
        timeout_seconds=timeout_seconds,
        model=model,
    )
    base_commit = resolve_ref(repo_root, base_ref)
    task_id = make_task_id(task_file)
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
    )
    _write_state(
        paths,
        status=TaskStatus.WORKER_FINISHED,
        task_id=task_id,
        base_ref=base_ref,
        base_commit=base_commit,
        config=config,
        extra={
            "worker": {
                "returncode": worker_result.returncode,
                "timed_out": worker_result.timed_out,
                "events_file": str(paths.worker_events_file),
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
    test_results = _run_tests(paths, config.test_commands, config.test_timeout_seconds)
    changed_files = collect_changed_files(worktree)
    patch = collect_patch(worktree)
    paths.patch_file.write_text(patch, encoding="utf-8")
    worker_failure = None
    if worker_result.timed_out:
        worker_failure = "Codex worker timed out."
    elif worker_result.returncode != 0:
        worker_failure = f"Codex worker exited with code {worker_result.returncode}."
    machine_review = run_machine_review(
        config,
        changed_files,
        patch,
        test_results,
        worker_ok=worker_failure is None,
        worker_failure_message=worker_failure,
    )

    final_status = TaskStatus.REVIEW_PENDING if machine_review.passed else TaskStatus.REVISION_REQUESTED
    payload = {
        "worker": {
            "returncode": worker_result.returncode,
            "timed_out": worker_result.timed_out,
            "events_file": str(paths.worker_events_file),
            "stderr_file": str(paths.worker_stderr_file),
            "final_file": str(paths.worker_final_file),
        },
        "changed_files": [item.to_json() for item in changed_files],
        "patch_file": str(paths.patch_file),
        "tests": [item.to_json() for item in test_results],
        "machine_review": machine_review.to_json(),
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
