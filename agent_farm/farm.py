from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import load_config, resolve_worker_profile
from .git_ops import find_repo_root, resolve_ref
from .models import TaskStatus
from .orchestrator import run_task
from .plans import WorkerPlan, WorkerPlanItem, read_supervisor_decision, read_worker_plan
from .supervisor import draft_supervisor_decision, synthesize_farm_deliverable
from .util import ensure_inside, read_json, write_json


class FarmError(RuntimeError):
    pass


def _make_farm_id(task_id: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{task_id}"


def _worker_summary(
    worker: WorkerPlanItem,
    result: dict[str, Any],
    profile_metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": worker.worker_id,
        "role": worker.role,
        "profile": worker.profile,
        "model": profile_metadata["model"],
        "provider": profile_metadata["provider"],
        "status": result.get("status"),
        "run_dir": result.get("run_dir"),
        "worktree": result.get("worktree"),
        "patch_file": result.get("patch_file"),
        "final_file": (result.get("worker") or {}).get("final_file"),
        "changed_files": result.get("changed_files", []),
        "tests": result.get("tests", []),
        "machine_review": result.get("machine_review", {}),
    }


def _run_plan_worker(
    *,
    repo_root: Path,
    config_path: Path | None,
    farm_id: str,
    plan: WorkerPlan,
    base_commit: str,
    worker: WorkerPlanItem,
    task_file: Path,
) -> dict[str, Any]:
    return run_task(
        repo=repo_root,
        task_file=task_file,
        config_path=config_path,
        base_ref=base_commit,
        allowed_paths=worker.allowed_paths or None,
        forbidden_paths=worker.forbidden_paths or None,
        test_commands=worker.test_commands or None,
        profile=worker.profile,
        task_id_override=f"{farm_id}-{worker.worker_id}",
    )


def run_farm(
    *,
    repo: Path,
    plan_file: Path,
    config_path: Path | None = None,
) -> dict[str, Any]:
    repo_root = find_repo_root(repo)
    config = load_config(repo_root, config_path)
    plan = read_worker_plan(plan_file)
    base_commit = resolve_ref(repo_root, plan.base_ref)

    if type(config.max_parallel_workers) is not int or config.max_parallel_workers < 1:
        raise FarmError("max_parallel_workers must be a positive integer.")

    resolved_profiles: dict[str, dict[str, Any]] = {}
    for worker in plan.workers:
        resolved, _ = resolve_worker_profile(config, worker.profile)
        resolved_profiles[worker.worker_id] = {
            "model": resolved.worker_model,
            "provider": resolved.worker_provider,
            "timeout_seconds": resolved.timeout_seconds,
        }
        if not worker.allowed_paths and not config.allowed_paths:
            raise FarmError(
                f"Worker '{worker.worker_id}' has no allowed_paths and the global "
                "config has no allowlist."
            )

    farm_id = _make_farm_id(plan.task_id)
    farm_dir = (repo_root / config.farms_dir / farm_id).resolve()
    ensure_inside(repo_root, farm_dir)
    tasks_dir = farm_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(plan_file, farm_dir / "worker-plan.input.json")
    write_json(farm_dir / "worker-plan.json", plan.to_json())

    task_files: dict[str, Path] = {}
    for worker in plan.workers:
        task_path = tasks_dir / f"{worker.worker_id}.md"
        task_path.write_text(worker.to_task_spec(), encoding="utf-8")
        task_files[worker.worker_id] = task_path

    initial = {
        "schema_version": 1,
        "farm_id": farm_id,
        "plan_task_id": plan.task_id,
        "status": TaskStatus.WORKER_RUNNING.value,
        "repo_root": str(repo_root),
        "farm_dir": str(farm_dir),
        "base_ref": plan.base_ref,
        "base_commit": base_commit,
        "worker_count": len(plan.workers),
        "profile_assignments": resolved_profiles,
        "workers": [],
    }
    write_json(farm_dir / "result.json", initial)

    requested_parallel = plan.max_parallel or config.max_parallel_workers
    max_workers = max(1, min(requested_parallel, len(plan.workers)))
    completed: dict[str, dict[str, Any]] = {}
    failures: dict[str, dict[str, str]] = {}

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="agent-farm") as executor:
        future_workers = {
            executor.submit(
                _run_plan_worker,
                repo_root=repo_root,
                config_path=config_path,
                farm_id=farm_id,
                plan=plan,
                base_commit=base_commit,
                worker=worker,
                task_file=task_files[worker.worker_id],
            ): worker
            for worker in plan.workers
        }
        for future in as_completed(future_workers):
            worker = future_workers[future]
            try:
                completed[worker.worker_id] = future.result()
            except Exception as exc:
                failures[worker.worker_id] = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                }

    worker_records: list[dict[str, Any]] = []
    passed_workers: list[str] = []
    for worker in plan.workers:
        result = completed.get(worker.worker_id)
        if result is None:
            worker_records.append(
                {
                    "id": worker.worker_id,
                    "role": worker.role,
                    "profile": worker.profile,
                    "model": resolved_profiles[worker.worker_id]["model"],
                    "provider": resolved_profiles[worker.worker_id]["provider"],
                    "status": "FAILED_TO_RUN",
                    "error": failures[worker.worker_id],
                }
            )
            continue
        summary = _worker_summary(worker, result, resolved_profiles[worker.worker_id])
        worker_records.append(summary)
        if summary["machine_review"].get("status") == "passed":
            passed_workers.append(worker.worker_id)

    all_workers_passed = len(passed_workers) == len(plan.workers)
    status = (
        TaskStatus.SUPERVISOR_REVIEW_PENDING
        if passed_workers and (plan.deliverable is None or all_workers_passed)
        else TaskStatus.REVISION_REQUESTED
    )
    result_payload = {
        **initial,
        "status": status.value,
        "max_parallel": max_workers,
        "passed_workers": passed_workers,
        "workers": worker_records,
        "review_package": str(farm_dir / "review-package.json"),
        "supervisor_decision": str(farm_dir / "supervisor-decision.json"),
        "deliverable": plan.deliverable.to_json() if plan.deliverable else None,
    }
    review_package = {
        "schema_version": 1,
        "task_id": farm_id,
        "plan_task_id": plan.task_id,
        "base_commit": base_commit,
        "status": status.value,
        "workers": worker_records,
        "supervisor_contract": {
            "authority": "expensive-supervisor-only",
            "next_action": (
                "Integrate every passing Worker artifact into the final deliverable."
                if plan.deliverable is not None
                else "Inspect the candidate patches and evidence, then write "
                "supervisor-decision.json."
            ),
            "mode": "collaborative_synthesis" if plan.deliverable else "candidate_selection",
            "deliverable": plan.deliverable.to_json() if plan.deliverable else None,
        },
    }
    write_json(farm_dir / "review-package.json", review_package)
    write_json(farm_dir / "result.json", result_payload)
    if (
        config.auto_supervisor_review
        and plan.deliverable is not None
        and all_workers_passed
        and config.agent_backend == "native"
    ):
        try:
            deliverable = synthesize_farm_deliverable(
                repo_root=repo_root,
                farm_dir=farm_dir,
                plan=plan,
                config_path=config_path,
            )
            result_payload["status"] = TaskStatus.COMPLETED.value
            result_payload["deliverable"] = deliverable
            write_json(farm_dir / "result.json", result_payload)
            return result_payload
        except Exception as exc:
            result_payload["synthesis_error"] = {
                "type": type(exc).__name__,
                "message": str(exc),
            }
            result_payload["status"] = TaskStatus.REVISION_REQUESTED.value
            write_json(farm_dir / "result.json", result_payload)
            return result_payload
    if config.auto_supervisor_review and passed_workers and config.agent_backend == "native":
        try:
            decision = draft_supervisor_decision(
                repo_root=repo_root,
                farm_dir=farm_dir,
                config_path=config_path,
            )
            decision_input = farm_dir / "supervisor-decision.auto.json"
            write_json(decision_input, decision.to_json())
            return record_supervisor_decision(farm_dir, decision_input)
        except Exception as exc:
            result_payload["supervisor_review_error"] = {
                "type": type(exc).__name__,
                "message": str(exc),
            }
            write_json(farm_dir / "result.json", result_payload)
    return result_payload


def review_farm(farm_dir: Path) -> dict[str, Any]:
    result_file = farm_dir / "result.json"
    if not result_file.exists():
        raise FarmError(f"No result.json found in {farm_dir}")
    return read_json(result_file)


def record_supervisor_decision(
    farm_dir: Path,
    decision_file: Path,
) -> dict[str, Any]:
    result = review_farm(farm_dir)
    decision = read_supervisor_decision(decision_file)
    farm_id = result.get("farm_id")
    if decision.task_id != farm_id:
        raise FarmError(
            f"Supervisor decision task_id '{decision.task_id}' does not match farm_id '{farm_id}'."
        )

    workers = {item.get("id"): item for item in result.get("workers", [])}
    if decision.approved_worker and decision.approved_worker not in workers:
        raise FarmError(f"Unknown approved_worker: {decision.approved_worker}")
    if decision.decision == "approve_merge":
        approved = workers[decision.approved_worker]
        if approved.get("machine_review", {}).get("status") != "passed":
            raise FarmError("Supervisor cannot approve a worker that failed machine review.")

    stored_path = farm_dir / "supervisor-decision.json"
    write_json(stored_path, decision.to_json())
    if decision.decision == "approve_merge":
        result["status"] = TaskStatus.SUPERVISOR_APPROVED.value
    elif decision.decision == "request_revision":
        result["status"] = TaskStatus.REVISION_REQUESTED.value
    elif decision.decision == "reject":
        result["status"] = TaskStatus.REJECTED.value
    else:
        result["status"] = decision.decision.upper()
    result["supervisor_decision"] = str(stored_path)
    result["decision"] = decision.to_json()
    write_json(farm_dir / "result.json", result)
    return result
