from __future__ import annotations

"""Durable one-shot task runtime.

This is the local execution boundary used by the desktop client.  A task owns
one durable job/session, runs an expensive Supervisor planning pass, then
hands the validated plan to the Worker farm.  All model and tool events are
written before they are broadcast, so a reconnect can replay the same stream.
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .diagnostics import valid_correlation_id
from .farm import FarmCancelled, run_farm
from .plans import WorkerPlan, read_worker_plan
from .runtime_store import RuntimeStore
from .supervisor import SupervisorError, draft_worker_plan
from .util import ensure_inside, write_json


TERMINAL_TASK_STATUSES = {"COMPLETED", "FAILED", "INTERRUPTED", "CANCELLED"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskRuntime:
    """Run a complete Supervisor -> Worker task in the local daemon."""

    def __init__(self, state: Any) -> None:
        self.state = state
        self.store: RuntimeStore = state.runtime_store
        self._condition = threading.Condition()
        self._generation = 0
        self._cancel_lock = threading.RLock()
        self._cancel_events: dict[str, threading.Event] = {}
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="agent-farm-task")
        recovered = self.store.interrupt_active_jobs(
            "task",
            interrupted_at=_utc_now(),
            message="The Agent Farm runtime stopped before the task completed.",
        )
        self.recovered_count = len(recovered)

    def submit(self, payload: dict[str, Any], *, correlation_id: str | None = None) -> dict[str, Any]:
        allowed = {"request", "task_id", "base_ref", "worker_count", "attachments", "thread_id", "turn_id"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValueError("Unknown task fields: " + ", ".join(unknown))
        request = payload.get("request")
        if not isinstance(request, str) or not request.strip():
            raise ValueError("request must be a non-empty string.")
        task_id = payload.get("task_id")
        if task_id is not None and (not isinstance(task_id, str) or not task_id.strip()):
            raise ValueError("task_id must be a non-empty string or null.")
        base_ref = payload.get("base_ref", "HEAD")
        if not isinstance(base_ref, str) or not base_ref.strip():
            raise ValueError("base_ref must be a non-empty string.")
        worker_count = payload.get("worker_count", 3)
        if type(worker_count) is not int or not 1 <= worker_count <= 12:
            raise ValueError("worker_count must be between 1 and 12.")
        attachments = payload.get("attachments", [])
        attachment_items = self.state.attachments.public_items(attachments)
        job_id = f"task-{uuid.uuid4().hex[:20]}"
        now = _utc_now()
        session_id = f"session-{job_id}"
        job = {
            "schema_version": 1,
            "job_id": job_id,
            "session_id": session_id,
            "parent_session_id": None,
            "role": "supervisor",
            "status": "QUEUED",
            "phase": "queued",
            "attempt": 0,
            "resume_from": None,
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "request": request.strip(),
            "task_id": task_id,
            "base_ref": base_ref,
            "worker_count": worker_count,
            "attachment_ids": list(attachments),
            "attachments": attachment_items,
            "thread_id": payload.get("thread_id"),
            "turn_id": payload.get("turn_id"),
            "farm_id": None,
            "plan_file": None,
            "result": None,
            "error": None,
            "correlation_id": valid_correlation_id(correlation_id),
        }
        self.store.create_job("task", job)
        with self._cancel_lock:
            self._cancel_events[job_id] = threading.Event()
        self._append(job_id, {"type": "task.queued", "status": "queued"})
        self._executor.submit(self._run, job_id)
        return self.get(job_id)

    def get(self, job_id: str) -> dict[str, Any]:
        return self.store.get_job("task", job_id)

    def recent(self) -> list[dict[str, Any]]:
        return self.store.recent_jobs("task")

    def events(self, job_id: str, *, after: int = 0) -> dict[str, Any]:
        if type(after) is not int or after < 0:
            raise ValueError("after must be a non-negative integer.")
        job = self.get(job_id)
        events = self.store.events("task", job_id, after=after)
        return {
            "job_id": job_id,
            "session_id": job.get("session_id"),
            "events": events,
            "next_sequence": events[-1]["sequence"] if events else after,
            "status": job["status"],
        }

    def generation(self) -> int:
        with self._condition:
            return self._generation

    def wait_for_change(self, generation: int, timeout: float) -> bool:
        with self._condition:
            return self._condition.wait_for(lambda: self._generation != generation, timeout=timeout)

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job["status"] in TERMINAL_TASK_STATUSES:
            return job
        with self._cancel_lock:
            event = self._cancel_events.get(job_id)
            if event is None:
                raise RuntimeError("The task is not active in this runtime.")
            event.set()
        self._append(job_id, {"type": "task.cancel_requested", "status": "cancelling"})
        self._update(job_id, status="CANCELLING")
        return self.get(job_id)

    def resume(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job["status"] not in {"FAILED", "INTERRUPTED", "CANCELLED"}:
            raise RuntimeError("Only stopped tasks can be resumed.")
        resume_from = "planning"
        if self._read_persisted_plan(job) is not None:
            # A completed Supervisor plan is an input to the Worker farm, not
            # disposable UI state. Reuse it after a restart/cancel so resume
            # does not spend another premium planning request or change the
            # user's approved decomposition.
            resume_from = "workers"
        with self._cancel_lock:
            self._cancel_events[job_id] = threading.Event()
        self._update(
            job_id,
            status="QUEUED",
            phase="queued",
            attempt=int(job.get("attempt") or 0) + 1,
            resume_from=resume_from,
            error=None,
            finished_at=None,
        )
        self._append(
            job_id,
            {
                "type": "task.resumed",
                "status": "queued",
                "resume_from": resume_from,
            },
        )
        self._executor.submit(self._run, job_id)
        return self.get(job_id)

    def close(self) -> None:
        with self._cancel_lock:
            for event in self._cancel_events.values():
                event.set()
        self._executor.shutdown(wait=False, cancel_futures=False)

    def _run(self, job_id: str) -> None:
        job = self.get(job_id)
        with self._cancel_lock:
            cancel_event = self._cancel_events[job_id]
        try:
            self._update(
                job_id,
                status="RUNNING",
                phase="planning",
                attempt=max(1, int(job.get("attempt") or 0)),
                started_at=_utc_now(),
                error=None,
            )
            task_root = (self.state.repo_root / ".agent-farm" / "tasks" / job_id).resolve()
            ensure_inside(self.state.repo_root, task_root)
            task_root.mkdir(parents=True, exist_ok=True)
            plan_file = task_root / "worker-plan.json"
            plan = self._read_persisted_plan(job)
            if plan is not None:
                plan_file = self._plan_path(task_root)
                self._update(job_id, phase="workers", plan_file=str(plan_file))
                self._append(
                    job_id,
                    {
                        "type": "task.plan.reused",
                        "phase": "planning",
                        "plan_file": str(plan_file),
                        "reason": "resume",
                    },
                )
            else:
                self._append(
                    job_id,
                    {"type": "task.phase", "phase": "planning", "status": "running"},
                )
                plan = draft_worker_plan(
                    repo_root=self.state.repo_root,
                    request=job["request"],
                    task_id=job.get("task_id"),
                    base_ref=job["base_ref"],
                    worker_count=job["worker_count"],
                    config_path=self.state.config_path,
                    output_dir=task_root / "supervisor",
                    attachment_context=self.state.attachments.context_for(job.get("attachment_ids", [])),
                    model_attachments=self.state.attachments.model_inputs_for(job.get("attachment_ids", [])),
                    event_callback=lambda event: self._forward(job_id, event),
                    cancel_check=cancel_event.is_set,
                )
                write_json(plan_file, plan.to_json())
                self._update(job_id, plan_file=str(plan_file), phase="planning")
                self._append(
                    job_id,
                    {
                        "type": "task.plan.ready",
                        "phase": "planning",
                        "plan_file": str(plan_file),
                    },
                )
            if cancel_event.is_set():
                raise FarmCancelled("Task execution was cancelled.")
            self._update(job_id, phase="workers")
            self._append(job_id, {"type": "task.phase", "phase": "workers", "status": "running"})
            farm_result = run_farm(
                repo=self.state.repo_root,
                plan_file=plan_file,
                config_path=self.state.config_path,
                event_callback=lambda event: self._forward(job_id, event),
                cancel_check=cancel_event.is_set,
                attachment_context=self.state.attachments.context_for(job.get("attachment_ids", [])),
                model_attachments=self.state.attachments.model_inputs_for(job.get("attachment_ids", [])),
                attachment_contexts=self.state.attachments.contexts_by_id(job.get("attachment_ids", [])),
                model_attachments_by_id=self.state.attachments.model_inputs_by_id(job.get("attachment_ids", [])),
            )
            farm_id = farm_result.get("farm_id")
            farm_status = str(farm_result.get("status") or "").upper()
            if farm_status in {"COMPLETED", "SUPERVISOR_APPROVED", "MERGED"}:
                self._append(job_id, {"type": "task.completed", "status": "completed", "farm_id": farm_id})
                self._update(
                    job_id,
                    status="COMPLETED",
                    phase="completed",
                    finished_at=_utc_now(),
                    farm_id=farm_id,
                    result=farm_result,
                )
            else:
                message = (
                    str((farm_result.get("synthesis_error") or {}).get("message") or "")
                    or str((farm_result.get("supervisor_review_error") or {}).get("message") or "")
                    or f"Farm finished in {farm_status or 'an unresolved'} state."
                )
                error = {"type": "FarmIncomplete", "message": message}
                self._append(
                    job_id,
                    {"type": "task.failed", "status": "failed", "farm_id": farm_id, "error": error},
                )
                self._update(
                    job_id,
                    status="FAILED",
                    phase="review",
                    finished_at=_utc_now(),
                    farm_id=farm_id,
                    result=farm_result,
                    error=error,
                )
        except (FarmCancelled, SupervisorError) as exc:
            self._finish(job_id, "CANCELLED" if isinstance(exc, FarmCancelled) else "FAILED", str(exc))
        except Exception as exc:  # the task boundary must persist failures for resume
            status = "CANCELLED" if cancel_event.is_set() else "FAILED"
            self._finish(job_id, status, str(exc))
        finally:
            with self._condition:
                self._generation += 1
                self._condition.notify_all()

    def _forward(self, job_id: str, event: dict[str, Any]) -> None:
        payload = dict(event)
        payload.setdefault("session_id", self.get(job_id).get("session_id"))
        self._append(job_id, payload)

    def _plan_path(self, task_root: Path) -> Path:
        plan_file = (task_root / "worker-plan.json").resolve()
        ensure_inside(task_root, plan_file)
        return plan_file

    def _read_persisted_plan(self, job: dict[str, Any]) -> WorkerPlan | None:
        raw_plan_file = job.get("plan_file")
        if not isinstance(raw_plan_file, str) or not raw_plan_file.strip():
            return None
        task_root = (self.state.repo_root / ".agent-farm" / "tasks" / str(job["job_id"])).resolve()
        ensure_inside(self.state.repo_root, task_root)
        plan_file = Path(raw_plan_file).resolve()
        ensure_inside(task_root, plan_file)
        if plan_file.name != "worker-plan.json" or not plan_file.is_file():
            return None
        try:
            return read_worker_plan(plan_file)
        except (OSError, ValueError, TypeError):
            # A corrupt or manually removed checkpoint cannot be trusted as a
            # continuation input. The next run will perform a fresh plan and
            # persist a new checkpoint instead of executing an invalid plan.
            return None

    def _finish(self, job_id: str, status: str, message: str) -> None:
        error = {"type": "TaskRuntimeError", "message": message}
        self._append(job_id, {"type": "task.cancelled" if status == "CANCELLED" else "task.failed", "status": status.lower(), "error": error})
        self._update(
            job_id,
            status=status,
            phase="cancelled" if status == "CANCELLED" else "failed",
            finished_at=_utc_now(),
            error=error,
        )

    def _update(self, job_id: str, **changes: Any) -> None:
        self.store.update_job("task", job_id, changes, updated_at=_utc_now())
        with self._condition:
            self._generation += 1
            self._condition.notify_all()

    def _append(self, job_id: str, event: dict[str, Any]) -> None:
        event.setdefault("timestamp", _utc_now())
        event.setdefault("session_id", self.get(job_id).get("session_id"))
        self.store.append_event("task", job_id, event)
