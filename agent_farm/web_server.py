from __future__ import annotations

import json
import os
import ipaddress
import socket
import shutil
import sys
import threading
import time
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from .approvals import ApprovalBroker
from .attachments import AttachmentStore
from .change_control import ChangeController, build_change_set
from .checkpoints import CheckpointStore
from .config import (
    AGENT_BACKENDS,
    APPROVAL_POLICIES,
    BUDGET_POLICIES,
    LOCAL_CONFIG_FILE,
    MODEL_PROVIDER_FIELDS,
    SANDBOX_MODES,
    config_from_dict,
    load_config,
    resolve_worker_profile,
    write_local_config,
)
from .farm import record_supervisor_decision, review_farm, run_farm
from .git_ops import find_repo_root
from .model_client import DEFAULT_PROVIDERS
from .plans import SupervisorDecision, WorkerPlan, read_worker_plan
from .protocol import (
    ProtocolNegotiationError,
    negotiate_protocol,
    protocol_descriptor,
)
from .provider_catalog import discover_provider_models
from .provider_templates import provider_templates
from .daemon_runtime import RUNTIME_PROTOCOL_VERSION
from .crash_recovery import CrashRecoveryReporter
from .diagnostics import (
    StructuredLogger,
    create_diagnostic_bundle,
    valid_correlation_id,
)
from .retention import ArtifactRetentionManager
from .runtime_store import RuntimeStore
from .secrets import load_secrets_env, update_secrets_env
from .supervisor import draft_worker_plan
from .threads import ThreadStore
from .usage import price_catalog
from .usage_report import farm_usage_report
from .util import ensure_inside, read_json, run_command, write_json

MAX_JSON_BODY = 1_000_000
MAX_ARTIFACT_BYTES = 2_000_000
TERMINAL_JOB_STATUSES = {"COMPLETED", "FAILED", "INTERRUPTED", "CANCELLED"}
ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
PROVIDER_SETTINGS_FIELDS = {
    "template_id",
    "name",
    "base_url",
    "env_key",
    "wire_api",
    "request_max_retries",
    "requires_openai_auth",
    "stream_idle_timeout_ms",
    "stream_max_retries",
    "supports_websockets",
}
PROVIDER_PRIVATE_FIELDS = MODEL_PROVIDER_FIELDS - PROVIDER_SETTINGS_FIELDS


class WebConsoleError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str, label: str) -> str:
    if not value or len(value) > 160 or any(char not in ID_CHARS for char in value):
        raise WebConsoleError(f"Invalid {label}.")
    return value


def _branch_name(repo_root: Path) -> str:
    result = run_command(["git", "branch", "--show-current"], repo_root)
    return result.stdout.strip() if result.ok and result.stdout.strip() else "detached HEAD"


def _local_endpoint_reachable(base_url: Any) -> bool | None:
    """Probe only loopback endpoints; never create outbound Settings traffic."""
    if not isinstance(base_url, str) or not base_url:
        return None
    parsed = urlsplit(base_url)
    host = parsed.hostname
    if not host:
        return None
    try:
        is_loopback = host.casefold() == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        return None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def _sync_interrupted_thread(state: "ConsoleState", job: dict[str, Any]) -> None:
    thread_id = job.get("thread_id")
    turn_id = job.get("turn_id")
    item_id = job.get("item_id")
    if not all(isinstance(value, str) and value for value in (thread_id, turn_id, item_id)):
        return
    try:
        thread = state.threads.read(thread_id)
        turn = next(
            (candidate for candidate in thread.get("turns", []) if candidate.get("turn_id") == turn_id),
            None,
        )
        item = next(
            (candidate for candidate in (turn or {}).get("items", []) if candidate.get("item_id") == item_id),
            None,
        )
        if item is None or item.get("status") not in {"queued", "running"}:
            return
        payload = dict(item.get("payload") or {})
        payload["error"] = job.get("error")
        state.threads.update_item(
            thread_id,
            turn_id,
            item_id,
            status="interrupted",
            payload=payload,
        )
        state.threads.update_turn(thread_id, turn_id, "interrupted")
    except (FileNotFoundError, ValueError):
        # Runtime recovery must not prevent the repository from opening if an
        # independently persisted thread was removed or became unreadable.
        return


class JobRegistry:
    def __init__(self, state: "ConsoleState") -> None:
        self._state = state
        self._store = state.runtime_store
        self._condition = threading.Condition()
        self._generation = 0
        self._cancel_lock = threading.RLock()
        self._cancel_events: dict[str, tuple[threading.Event, dict[str, threading.Event]]] = {}
        # Each farm already parallelizes workers. Serializing farms prevents a browser
        # double-click from unexpectedly multiplying model spend.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="farm-ui")
        recovered = self._store.interrupt_active_jobs(
            "farm",
            interrupted_at=_utc_now(),
            message="The Agent Farm runtime stopped before this Farm completed.",
        )
        self.recovered_count = len(recovered)
        for job in recovered:
            _sync_interrupted_thread(state, job)

    def submit(
        self,
        raw_plan: dict[str, Any],
        *,
        thread_id: str | None = None,
        turn_id: str | None = None,
        attachment_ids: list[str] | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        plan = WorkerPlan.from_dict(raw_plan)
        attachment_ids = list(attachment_ids or [])
        attachment_items = self._state.attachments.public_items(attachment_ids)
        farm_item_id: str | None = None
        if thread_id is not None:
            if not isinstance(thread_id, str):
                raise ValueError("thread_id must be a string or null.")
            self._state.threads.read(thread_id)
            if turn_id is None:
                turn = self._state.threads.start_turn(thread_id, plan.task_id)
                turn_id = turn["turn_id"]
            elif not isinstance(turn_id, str):
                raise ValueError("turn_id must be a string or null.")
            item = self._state.threads.add_item(
                thread_id,
                turn_id,
                "farm_run",
                status="queued",
                payload={
                    "task_id": plan.task_id,
                    "farm_id": None,
                    "attachments": attachment_items,
                },
            )
            farm_item_id = item["item_id"]
        job_id = uuid.uuid4().hex[:12]
        with self._cancel_lock:
            self._cancel_events[job_id] = (
                threading.Event(),
                {worker.worker_id: threading.Event() for worker in plan.workers},
            )
        plan_file = self._state.submissions_dir / f"{job_id}.json"
        write_json(plan_file, plan.to_json())
        job = {
            "schema_version": 1,
            "job_id": job_id,
            "task_id": plan.task_id,
            "status": "QUEUED",
            "created_at": _utc_now(),
            "started_at": None,
            "finished_at": None,
            "farm_id": None,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "item_id": farm_item_id,
            "attachment_ids": attachment_ids,
            "attachments": attachment_items,
            "error": None,
            "correlation_id": valid_correlation_id(correlation_id),
        }
        self._store.create_job("farm", job)
        self._state.logger.log(
            "farm.queued",
            correlation_id=job["correlation_id"],
            job_id=job_id,
            task_id=plan.task_id,
            worker_count=len(plan.workers),
        )
        self._executor.submit(self._run, job_id, plan_file)
        return dict(job)

    def _run(self, job_id: str, plan_file: Path) -> None:
        self._update(job_id, status="RUNNING", started_at=_utc_now())
        job = self.get(job_id)
        self._state.logger.log(
            "farm.started", correlation_id=job.get("correlation_id"), job_id=job_id
        )
        if job.get("thread_id") and job.get("turn_id") and job.get("item_id"):
            self._state.threads.update_item(
                job["thread_id"],
                job["turn_id"],
                job["item_id"],
                status="running",
                payload={"task_id": job["task_id"], "farm_id": None},
            )
            self._state.threads.update_turn(job["thread_id"], job["turn_id"], "running")
        try:
            with self._cancel_lock:
                job_cancel, worker_cancels = self._cancel_events[job_id]
            result = run_farm(
                repo=self._state.repo_root,
                plan_file=plan_file,
                config_path=self._state.config_path,
                event_callback=lambda event: self._append_event(job_id, event),
                approval_callback=lambda request: self._state.approvals.request(
                    job_kind="farm",
                    job_id=job_id,
                    request=request,
                    event_callback=lambda event: self._append_event(job_id, event),
                ),
                cancel_check=job_cancel.is_set,
                worker_cancel_checks={
                    worker_id: event.is_set for worker_id, event in worker_cancels.items()
                },
                attachment_context=self._state.attachments.context_for(job.get("attachment_ids")),
                model_attachments=self._state.attachments.model_inputs_for(job.get("attachment_ids")),
                attachment_contexts=self._state.attachments.contexts_by_id(job.get("attachment_ids")),
                model_attachments_by_id=self._state.attachments.model_inputs_by_id(
                    job.get("attachment_ids")
                ),
            )
        except Exception as exc:
            if self._job_cancelled(job_id):
                self._finish_cancelled(job_id, job)
                return
            error = {"type": type(exc).__name__, "message": str(exc)}
            if job.get("thread_id") and job.get("turn_id") and job.get("item_id"):
                self._state.threads.update_item(
                    job["thread_id"], job["turn_id"], job["item_id"], status="failed", payload={"error": error}
                )
                self._state.threads.update_turn(job["thread_id"], job["turn_id"], "failed")
            self._update(
                job_id,
                status="FAILED",
                finished_at=_utc_now(),
                error=error,
            )
            self._state.logger.log(
                "farm.failed",
                level="ERROR",
                correlation_id=job.get("correlation_id"),
                job_id=job_id,
                error=error,
            )
            return
        if self._job_cancelled(job_id):
            self._finish_cancelled(job_id, job)
            return
        if job.get("thread_id") and job.get("turn_id") and job.get("item_id"):
            self._state.threads.update_item(
                job["thread_id"],
                job["turn_id"],
                job["item_id"],
                status="completed",
                payload={
                    "task_id": job["task_id"],
                    "farm_id": result.get("farm_id"),
                    "farm_status": result.get("status"),
                    "decision": result.get("decision"),
                },
            )
            decision = result.get("decision")
            if isinstance(decision, dict):
                turn_status = (
                    "awaiting_user"
                    if decision.get("decision") == "hold_for_user"
                    else "completed"
                )
            else:
                turn_status = "awaiting_review"
            self._state.threads.update_turn(job["thread_id"], job["turn_id"], turn_status)
        self._update(
            job_id,
            status="COMPLETED",
            finished_at=_utc_now(),
            farm_id=result.get("farm_id"),
        )
        self._state.logger.log(
            "farm.completed",
            correlation_id=job.get("correlation_id"),
            job_id=job_id,
            farm_id=result.get("farm_id"),
        )

    def _job_cancelled(self, job_id: str) -> bool:
        with self._cancel_lock:
            cancellation = self._cancel_events.get(job_id)
            return cancellation is not None and cancellation[0].is_set()

    def _finish_cancelled(self, job_id: str, job: dict[str, Any]) -> None:
        error = {"type": "Cancelled", "message": "Farm execution was cancelled by the user."}
        if job.get("thread_id") and job.get("turn_id") and job.get("item_id"):
            self._state.threads.update_item(
                job["thread_id"],
                job["turn_id"],
                job["item_id"],
                status="cancelled",
                payload={"error": error},
            )
            self._state.threads.update_turn(job["thread_id"], job["turn_id"], "cancelled")
        self._append_event(job_id, {"type": "job.cancelled", "error": error["message"]})
        self._update(job_id, status="CANCELLED", finished_at=_utc_now(), error=error)

    def cancel(self, job_id: str, *, worker_id: str | None = None) -> dict[str, Any]:
        job = self.get(job_id)
        if job["status"] in TERMINAL_JOB_STATUSES:
            raise WebConsoleError("The Farm job has already finished.")
        with self._cancel_lock:
            try:
                job_event, worker_events = self._cancel_events[job_id]
            except KeyError as exc:
                raise WebConsoleError("The Farm job is not active in this runtime.") from exc
            if worker_id is None:
                job_event.set()
                for event in worker_events.values():
                    event.set()
            else:
                _safe_id(worker_id, "worker id")
                try:
                    worker_events[worker_id].set()
                except KeyError as exc:
                    raise FileNotFoundError("Unknown Worker for this Farm job.") from exc
        self._state.approvals.cancel_job("farm", job_id, agent_id=worker_id)
        event = {
            "type": "worker.cancel_requested" if worker_id else "job.cancel_requested",
            "status": "cancelling",
        }
        if worker_id:
            event.update({"agent_id": worker_id, "agent_kind": "worker"})
        self._append_event(job_id, event)
        return {
            "job_id": job_id,
            "worker_id": worker_id,
            "status": "CANCELLING",
        }

    def retry(self, job_id: str, *, worker_id: str | None = None) -> dict[str, Any]:
        previous = self.get(job_id)
        if previous["status"] not in TERMINAL_JOB_STATUSES:
            raise WebConsoleError("The Farm job must finish before it can be retried.")
        plan_file = self._state.submissions_dir / f"{job_id}.json"
        plan = read_worker_plan(plan_file)
        if worker_id is not None:
            _safe_id(worker_id, "worker id")
            selected = next(
                (worker for worker in plan.workers if worker.worker_id == worker_id),
                None,
            )
            if selected is None:
                raise FileNotFoundError("Unknown Worker for this Farm job.")
            selected_payload = selected.to_json()
            selected_payload["depends_on"] = []
            retry_plan = {
                "schema_version": 1,
                "task_id": f"{plan.task_id}-retry-{worker_id}",
                "base_ref": plan.base_ref,
                "max_parallel": 1,
                "workers": [selected_payload],
                "deliverable": None,
            }
        else:
            retry_plan = plan.to_json()
            retry_plan["task_id"] = f"{plan.task_id}-retry"
        retried = self.submit(
            retry_plan,
            thread_id=previous.get("thread_id"),
            attachment_ids=previous.get("attachment_ids"),
            correlation_id=previous.get("correlation_id"),
        )
        self._update(
            retried["job_id"],
            retry_of=job_id,
            retry_worker_id=worker_id,
        )
        return self.get(retried["job_id"])

    def _update(self, job_id: str, **changes: Any) -> None:
        self._store.update_job("farm", job_id, changes, updated_at=_utc_now())
        self._notify_change()

    def get(self, job_id: str) -> dict[str, Any]:
        _safe_id(job_id, "job id")
        return self._store.get_job("farm", job_id)

    def _append_event(self, job_id: str, event: dict[str, Any]) -> None:
        payload = dict(event)
        payload.setdefault("schema_version", 1)
        local_sequence = payload.pop("sequence", None)
        if local_sequence is not None:
            payload["local_sequence"] = local_sequence
        payload.setdefault("timestamp", _utc_now())
        try:
            self._store.append_event("farm", job_id, payload)
        except FileNotFoundError:
            return
        self._notify_change()

    def generation(self) -> int:
        with self._condition:
            return self._generation

    def wait_for_change(self, generation: int, timeout: float) -> bool:
        with self._condition:
            return self._condition.wait_for(
                lambda: self._generation != generation,
                timeout=timeout,
            )

    def _notify_change(self) -> None:
        with self._condition:
            self._generation += 1
            self._condition.notify_all()

    def events(self, job_id: str, *, after: int = 0) -> dict[str, Any]:
        _safe_id(job_id, "job id")
        if after < 0:
            raise ValueError("after must be zero or greater.")
        job = self.get(job_id)
        events = self._store.events("farm", job_id, after=after)
        next_sequence = events[-1]["sequence"] if events else after
        return {"events": events, "next_sequence": next_sequence, "status": job["status"]}

    def recent(self) -> list[dict[str, Any]]:
        return self._store.recent_jobs("farm")

    def close(self) -> None:
        with self._cancel_lock:
            for job_event, worker_events in self._cancel_events.values():
                job_event.set()
                for event in worker_events.values():
                    event.set()
        self._executor.shutdown(wait=False, cancel_futures=False)


class PlanJobRegistry:
    def __init__(self, state: "ConsoleState") -> None:
        self._state = state
        self._store = state.runtime_store
        self._condition = threading.Condition()
        self._generation = 0
        self._cancel_lock = threading.RLock()
        self._cancel_events: dict[str, threading.Event] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="supervisor-ui")
        recovered = self._store.interrupt_active_jobs(
            "plan",
            interrupted_at=_utc_now(),
            message="The Agent Farm runtime stopped before planning completed.",
        )
        self.recovered_count = len(recovered)
        for job in recovered:
            _sync_interrupted_thread(state, job)

    def submit(
        self, payload: dict[str, Any], *, correlation_id: str | None = None
    ) -> dict[str, Any]:
        known = {"request", "task_id", "base_ref", "worker_count", "thread_id", "attachments"}
        unknown = sorted(set(payload) - known)
        if unknown:
            raise ValueError("Unknown planning fields: " + ", ".join(unknown))
        request = payload.get("request")
        task_id = payload.get("task_id")
        base_ref = payload.get("base_ref", "HEAD")
        worker_count = payload.get("worker_count", 3)
        thread_id = payload.get("thread_id")
        attachment_ids = payload.get("attachments", [])
        attachment_items = self._state.attachments.public_items(attachment_ids)
        if not isinstance(request, str) or not request.strip():
            raise ValueError("request must be a non-empty string.")
        if task_id is not None and not isinstance(task_id, str):
            raise ValueError("task_id must be a string or null.")
        if not isinstance(base_ref, str) or not base_ref:
            raise ValueError("base_ref must be a non-empty string.")
        if type(worker_count) is not int or not 1 <= worker_count <= 12:
            raise ValueError("worker_count must be between 1 and 12.")
        turn_id: str | None = None
        plan_item_id: str | None = None
        if thread_id is not None:
            if not isinstance(thread_id, str):
                raise ValueError("thread_id must be a string or null.")
            self._state.threads.read(thread_id)
            turn = self._state.threads.start_turn(
                thread_id,
                request,
                attachments=attachment_items,
            )
            turn_id = turn["turn_id"]
            item = self._state.threads.add_item(
                thread_id,
                turn_id,
                "supervisor_plan",
                status="queued",
                payload={"request": request, "attachments": attachment_items},
            )
            plan_item_id = item["item_id"]

        job_id = uuid.uuid4().hex[:12]
        with self._cancel_lock:
            self._cancel_events[job_id] = threading.Event()
        job = {
            "schema_version": 1,
            "job_id": job_id,
            "status": "QUEUED",
            "created_at": _utc_now(),
            "started_at": None,
            "finished_at": None,
            "plan": None,
            "thread_id": thread_id,
            "turn_id": turn_id,
            "item_id": plan_item_id,
            "attachment_ids": list(attachment_ids),
            "attachments": attachment_items,
            "error": None,
            "correlation_id": valid_correlation_id(correlation_id),
        }
        self._store.create_job("plan", job)
        self._state.logger.log(
            "plan.queued",
            correlation_id=job["correlation_id"],
            job_id=job_id,
            worker_count=worker_count,
        )
        self._executor.submit(
            self._run,
            job_id,
            request.strip(),
            task_id,
            base_ref,
            worker_count,
            list(attachment_ids),
        )
        return dict(job)

    def _run(
        self,
        job_id: str,
        request: str,
        task_id: str | None,
        base_ref: str,
        worker_count: int,
        attachment_ids: list[str],
    ) -> None:
        self._update(job_id, status="RUNNING", started_at=_utc_now())
        job = self.get(job_id)
        self._state.logger.log(
            "plan.started", correlation_id=job.get("correlation_id"), job_id=job_id
        )
        if job.get("thread_id") and job.get("turn_id") and job.get("item_id"):
            self._state.threads.update_item(
                job["thread_id"],
                job["turn_id"],
                job["item_id"],
                status="running",
                payload={
                    "request": request,
                    "attachments": self._state.attachments.public_items(attachment_ids),
                },
            )
        try:
            with self._cancel_lock:
                cancel_event = self._cancel_events[job_id]
            plan = draft_worker_plan(
                repo_root=self._state.repo_root,
                request=request,
                task_id=task_id,
                base_ref=base_ref,
                worker_count=worker_count,
                config_path=self._state.config_path,
                output_dir=self._state.submissions_dir / f"supervisor-{job_id}",
                attachment_context=self._state.attachments.context_for(attachment_ids),
                model_attachments=self._state.attachments.model_inputs_for(attachment_ids),
                event_callback=lambda event: self._append_event(
                    job_id,
                    {
                        **event,
                        "agent_id": "supervisor",
                        "agent_kind": "supervisor",
                        "display_name": "Planning Supervisor",
                    },
                ),
                approval_callback=lambda request: self._state.approvals.request(
                    job_kind="plan",
                    job_id=job_id,
                    request={
                        **request,
                        "agent_id": "supervisor",
                        "agent_kind": "supervisor",
                        "display_name": "Planning Supervisor",
                        "provider": self._state.config.supervisor_provider
                        or self._state.config.worker_provider,
                        "model": self._state.config.supervisor_model
                        or self._state.config.worker_model,
                    },
                    event_callback=lambda event: self._append_event(job_id, event),
                ),
                cancel_check=cancel_event.is_set,
            )
        except Exception as exc:
            if self._job_cancelled(job_id):
                self._finish_cancelled(job_id, job)
                return
            error = {"type": type(exc).__name__, "message": str(exc)}
            if job.get("thread_id") and job.get("turn_id") and job.get("item_id"):
                self._state.threads.update_item(
                    job["thread_id"], job["turn_id"], job["item_id"], status="failed", payload={"error": error}
                )
                self._state.threads.update_turn(job["thread_id"], job["turn_id"], "failed")
            self._update(
                job_id,
                status="FAILED",
                finished_at=_utc_now(),
                error=error,
            )
            self._state.logger.log(
                "plan.failed",
                level="ERROR",
                correlation_id=job.get("correlation_id"),
                job_id=job_id,
                error=error,
            )
            return
        if self._job_cancelled(job_id):
            self._finish_cancelled(job_id, job)
            return
        if job.get("thread_id") and job.get("turn_id") and job.get("item_id"):
            self._state.threads.update_item(
                job["thread_id"],
                job["turn_id"],
                job["item_id"],
                status="completed",
                payload={"plan": plan.to_json()},
            )
            self._state.threads.update_turn(
                job["thread_id"], job["turn_id"], "awaiting_confirmation"
            )
        self._update(
            job_id,
            status="COMPLETED",
            finished_at=_utc_now(),
            plan=plan.to_json(),
        )
        self._state.logger.log(
            "plan.completed", correlation_id=job.get("correlation_id"), job_id=job_id
        )

    def _job_cancelled(self, job_id: str) -> bool:
        with self._cancel_lock:
            event = self._cancel_events.get(job_id)
            return event is not None and event.is_set()

    def _finish_cancelled(self, job_id: str, job: dict[str, Any]) -> None:
        error = {"type": "Cancelled", "message": "Supervisor planning was cancelled by the user."}
        if job.get("thread_id") and job.get("turn_id") and job.get("item_id"):
            self._state.threads.update_item(
                job["thread_id"],
                job["turn_id"],
                job["item_id"],
                status="cancelled",
                payload={"error": error},
            )
            self._state.threads.update_turn(job["thread_id"], job["turn_id"], "cancelled")
        self._append_event(job_id, {"type": "job.cancelled", "error": error["message"]})
        self._update(job_id, status="CANCELLED", finished_at=_utc_now(), error=error)

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job["status"] in TERMINAL_JOB_STATUSES:
            raise WebConsoleError("The planning job has already finished.")
        with self._cancel_lock:
            try:
                self._cancel_events[job_id].set()
            except KeyError as exc:
                raise WebConsoleError("The planning job is not active in this runtime.") from exc
        self._state.approvals.cancel_job("plan", job_id)
        self._append_event(
            job_id,
            {"type": "job.cancel_requested", "status": "cancelling"},
        )
        return {"job_id": job_id, "status": "CANCELLING"}

    def _update(self, job_id: str, **changes: Any) -> None:
        self._store.update_job("plan", job_id, changes, updated_at=_utc_now())
        self._notify_change()

    def get(self, job_id: str) -> dict[str, Any]:
        _safe_id(job_id, "plan job id")
        return self._store.get_job("plan", job_id)

    def _append_event(self, job_id: str, event: dict[str, Any]) -> None:
        payload = dict(event)
        payload.setdefault("schema_version", 1)
        local_sequence = payload.pop("sequence", None)
        if local_sequence is not None:
            payload["local_sequence"] = local_sequence
        payload.setdefault("timestamp", _utc_now())
        try:
            self._store.append_event("plan", job_id, payload)
        except FileNotFoundError:
            return
        self._notify_change()

    def generation(self) -> int:
        with self._condition:
            return self._generation

    def wait_for_change(self, generation: int, timeout: float) -> bool:
        with self._condition:
            return self._condition.wait_for(
                lambda: self._generation != generation,
                timeout=timeout,
            )

    def _notify_change(self) -> None:
        with self._condition:
            self._generation += 1
            self._condition.notify_all()

    def events(self, job_id: str, *, after: int = 0) -> dict[str, Any]:
        _safe_id(job_id, "plan job id")
        if after < 0:
            raise ValueError("after must be zero or greater.")
        job = self.get(job_id)
        events = self._store.events("plan", job_id, after=after)
        next_sequence = events[-1]["sequence"] if events else after
        return {"events": events, "next_sequence": next_sequence, "status": job["status"]}

    def recent(self) -> list[dict[str, Any]]:
        return self._store.recent_jobs("plan")

    def close(self) -> None:
        with self._cancel_lock:
            for event in self._cancel_events.values():
                event.set()
        self._executor.shutdown(wait=False, cancel_futures=False)


@dataclass
class ConsoleState:
    repo_root: Path
    config_path: Path | None

    def __post_init__(self) -> None:
        self.repo_root = self.repo_root.resolve()
        self.runtime_started_at = _utc_now()
        self.runtime_root = (self.repo_root / ".agent-farm").resolve()
        self.logger = StructuredLogger(self.runtime_root / "logs" / "events.jsonl")
        self.crash_recovery = CrashRecoveryReporter(self.runtime_root)
        self.crash_recovery.start()
        self._settings_lock = threading.RLock()
        self._model_catalog_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self.config = load_config(self.repo_root, self.config_path)
        self.farms_dir = (self.repo_root / self.config.farms_dir).resolve()
        self.submissions_dir = (self.repo_root / ".agent-farm" / "ui-submissions").resolve()
        self.attachments_dir = (self.repo_root / ".agent-farm" / "ui-attachments").resolve()
        ensure_inside(self.repo_root, self.farms_dir)
        ensure_inside(self.repo_root, self.submissions_dir)
        ensure_inside(self.repo_root, self.attachments_dir)
        self.submissions_dir.mkdir(parents=True, exist_ok=True)
        self.threads = ThreadStore(self.repo_root / ".agent-farm" / "threads")
        self.attachments = AttachmentStore(self.attachments_dir)
        self.runtime_store = RuntimeStore(self.runtime_root / "runtime.sqlite3")
        self.checkpoints = CheckpointStore(
            self.repo_root,
            self.repo_root / ".agent-farm" / "checkpoints",
        )
        self.change_controller = ChangeController(self.repo_root, self.checkpoints)
        self.approvals = ApprovalBroker()
        self.jobs = JobRegistry(self)
        self.plan_jobs = PlanJobRegistry(self)
        self.crash_recovery.record_reconciliation(
            self.jobs.recovered_count + self.plan_jobs.recovered_count
        )
        self.retention = ArtifactRetentionManager(
            self.runtime_root,
            retention_days=self.config.artifact_retention_days,
            max_backups=self.config.max_runtime_backups,
            max_diagnostics=self.config.max_diagnostic_bundles,
        )
        config_backup_path = (
            self.config_path.resolve()
            if self.config_path is not None
            else self.repo_root / LOCAL_CONFIG_FILE
        )
        retention_result = self.retention.maintain(config_path=config_backup_path)
        self.logger.log(
            "runtime.started",
            session_id=self.crash_recovery.session_id,
            recovered_jobs=self.jobs.recovered_count + self.plan_jobs.recovered_count,
            retention=retention_result,
        )

    def _reload_config(self) -> None:
        config = load_config(self.repo_root, self.config_path)
        farms_dir = (self.repo_root / config.farms_dir).resolve()
        ensure_inside(self.repo_root, farms_dir)
        self.config = config
        self.farms_dir = farms_dir
        self._model_catalog_cache.clear()

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "protocol_version": RUNTIME_PROTOCOL_VERSION,
            "app": {"name": "Agent Farm", "version": __version__},
            "pid": os.getpid(),
            "repository": str(self.repo_root),
            "started_at": self.runtime_started_at,
            "runtime_fingerprint": os.environ.get("AGENT_FARM_RUNTIME_FINGERPRINT", __version__),
            "recovery": self.crash_recovery.report,
        }

    def export_diagnostics(self) -> dict[str, Any]:
        bundle = create_diagnostic_bundle(
            self.repo_root,
            sanitized_config=self._sanitized_config(),
            recovery_report=self.crash_recovery.report,
        )
        self.retention.maintain()
        self.logger.log("diagnostics.exported", path=bundle["path"], size_bytes=bundle["size_bytes"])
        return bundle

    def initialize_protocol(self, payload: dict[str, Any]) -> dict[str, Any]:
        return negotiate_protocol(payload)

    def _sanitized_config(self) -> dict[str, Any]:
        data = self.config.to_json()
        providers: dict[str, dict[str, Any]] = {}
        for provider_id, raw_provider in self.config.model_providers.items():
            if not isinstance(raw_provider, dict):
                continue
            providers[provider_id] = {
                key: value
                for key, value in raw_provider.items()
                if key in PROVIDER_SETTINGS_FIELDS
            }
        data["model_providers"] = providers
        if not data["worker_profiles"]:
            legacy_profile = {
                "display_name": "Default Worker",
                "model": self.config.worker_model,
                "provider": self.config.worker_provider,
                "reasoning_mode": self.config.worker_reasoning_mode,
                "reasoning_effort": self.config.worker_reasoning_effort,
                "oss": self.config.worker_oss,
                "local_provider": self.config.worker_local_provider,
                "codex_profile": self.config.worker_codex_profile,
                "codex_profile_v2": self.config.worker_codex_profile_v2,
                "secrets_env": self.config.secrets_env,
                "timeout_seconds": self.config.timeout_seconds,
                "sandbox": self.config.sandbox,
                "approval_policy": self.config.approval_policy,
                "ephemeral": self.config.ephemeral,
                "codex_json": self.config.codex_json,
                "codex_config_overrides": dict(self.config.codex_config_overrides),
            }
            data["worker_profiles"] = {
                "default": {
                    key: value
                    for key, value in legacy_profile.items()
                    if value is not None
                }
            }
            data["default_worker_profile"] = "default"
        return data

    def settings(self) -> dict[str, Any]:
        with self._settings_lock:
            config = self._sanitized_config()
            secret_values: dict[str, str] = {}
            secret_error: str | None = None
            try:
                secret_values = load_secrets_env(self.repo_root, self.config.secrets_env)
            except (OSError, ValueError) as exc:
                secret_error = str(exc)
            provider_status: dict[str, dict[str, Any]] = {}
            providers_for_status = {
                provider_id: dict(provider)
                for provider_id, provider in DEFAULT_PROVIDERS.items()
            }
            for provider_id, provider in config["model_providers"].items():
                providers_for_status.setdefault(provider_id, {}).update(provider)
            for provider_id, provider in providers_for_status.items():
                env_key = provider.get("env_key")
                provider_status[provider_id] = {
                    "credential_configured": bool(
                        isinstance(env_key, str)
                        and env_key
                        and (secret_values.get(env_key) or os.environ.get(env_key))
                    ),
                    "uses_environment_credential": bool(env_key),
                    "endpoint_reachable": _local_endpoint_reachable(provider.get("base_url")),
                }
            binary = self.config.codex_binary
            binary_path = shutil.which(binary)
            if binary_path is None and Path(binary).is_file():
                binary_path = str(Path(binary).resolve())
            active_provider = self.config.supervisor_provider or self.config.worker_provider or "openai"
            active_status = provider_status.get(active_provider, {})
            provider_ready = active_provider in provider_status
            credential_ready = not active_status.get("uses_environment_credential") or bool(
                active_status.get("credential_configured")
            )
            endpoint_ready = active_status.get("endpoint_reachable") is not False
            native_ready = bool(
                (self.config.supervisor_model or self.config.worker_model)
                and provider_ready
                and credential_ready
                and endpoint_ready
            )
            return {
                "schema_version": 1,
                "app": {"name": "Agent Farm", "version": __version__},
                "config": config,
                "editable_path": LOCAL_CONFIG_FILE,
                "applies_to": "new runs",
                "migration_required": not bool(self.config.worker_profiles),
                "runtime": {
                    "active_backend": self.config.agent_backend,
                    "native_available": True,
                    "native_ready": native_ready,
                    "codex_available": binary_path is not None,
                    "codex_binary": binary,
                },
                "secrets": {
                    "configured": bool(secret_values),
                    "error": secret_error,
                },
                "provider_status": provider_status,
                "provider_templates": provider_templates(),
                "price_catalog": price_catalog(self.config.model_price_overrides),
                "options": {
                    "agent_backends": sorted(AGENT_BACKENDS),
                    "sandbox_modes": sorted(SANDBOX_MODES),
                    "approval_policies": sorted(APPROVAL_POLICIES),
                    "budget_policies": sorted(BUDGET_POLICIES),
                    "wire_apis": ["responses", "chat"],
                },
            }

    def provider_models(self, provider_id: str, *, refresh: bool = False) -> dict[str, Any]:
        _safe_id(provider_id, "provider id")
        with self._settings_lock:
            cached = self._model_catalog_cache.get(provider_id)
            if not refresh and cached and time.monotonic() - cached[0] < 300:
                result = dict(cached[1])
                result["cached"] = True
                return result
            result = discover_provider_models(
                config=self.config,
                repo_root=self.repo_root,
                provider_id=provider_id,
            )
            self._model_catalog_cache[provider_id] = (time.monotonic(), result)
            return dict(result)

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        unknown = sorted(set(payload) - {"config", "provider_secrets"})
        if unknown:
            raise ValueError("Unknown settings fields: " + ", ".join(unknown))
        incoming = payload.get("config")
        if not isinstance(incoming, dict):
            raise ValueError("config must be a JSON object.")
        provider_secrets = payload.get("provider_secrets", {})
        if not isinstance(provider_secrets, dict):
            raise ValueError("provider_secrets must be a JSON object.")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in provider_secrets.items()):
            raise ValueError("Each provider secret must be a named string.")
        with self._settings_lock:
            data = dict(incoming)
            raw_providers = data.get("model_providers")
            if not isinstance(raw_providers, dict):
                raise ValueError("model_providers must be a JSON object.")
            merged_providers: dict[str, dict[str, Any]] = {}
            for provider_id, raw_provider in raw_providers.items():
                if not isinstance(provider_id, str) or not isinstance(raw_provider, dict):
                    raise ValueError("Each model provider must be a named JSON object.")
                hidden = sorted(set(raw_provider) & PROVIDER_PRIVATE_FIELDS)
                if hidden:
                    raise ValueError(
                        "Sensitive provider fields cannot be edited here: " + ", ".join(hidden)
                    )
                merged = dict(raw_provider)
                existing = self.config.model_providers.get(provider_id, {})
                if isinstance(existing, dict):
                    for key in PROVIDER_PRIVATE_FIELDS:
                        if key in existing:
                            merged[key] = existing[key]
                merged_providers[provider_id] = merged
            data["model_providers"] = merged_providers
            validated = config_from_dict(data)
            secret_updates: dict[str, str] = {}
            for provider_id, secret in provider_secrets.items():
                provider = validated.model_providers.get(provider_id)
                if not isinstance(provider, dict):
                    raise ValueError(f"Cannot save an API key for unknown provider: {provider_id}")
                env_key = provider.get("env_key")
                if not isinstance(env_key, str) or not env_key:
                    raise ValueError(f"Provider '{provider_id}' does not use an API key.")
                existing_secret = secret_updates.get(env_key)
                if existing_secret is not None and existing_secret != secret:
                    raise ValueError(f"Conflicting API keys use the same environment variable: {env_key}")
                secret_updates[env_key] = secret
            if secret_updates:
                update_secrets_env(self.repo_root, validated.secrets_env, secret_updates)
            write_local_config(self.repo_root, validated.to_json())
            self._reload_config()
            return self.settings()

    def farm_dir(self, farm_id: str) -> Path:
        _safe_id(farm_id, "farm id")
        path = (self.farms_dir / farm_id).resolve()
        ensure_inside(self.farms_dir, path)
        if not path.is_dir():
            raise FileNotFoundError(f"Unknown farm: {farm_id}")
        return path

    def list_farms(self) -> list[dict[str, Any]]:
        if not self.farms_dir.exists():
            return []
        farms: list[dict[str, Any]] = []
        for result_file in self.farms_dir.glob("*/result.json"):
            try:
                result = read_json(result_file)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            farms.append(_farm_summary(result))
        return sorted(farms, key=lambda item: item.get("farm_id", ""), reverse=True)

    def farm_result(self, farm_id: str) -> dict[str, Any]:
        farm_dir = self.farm_dir(farm_id)
        result = review_farm(farm_dir)
        result["usage"] = farm_usage_report(self.repo_root, farm_dir, result)
        return result

    def review_package(self, farm_id: str) -> dict[str, Any]:
        path = self.farm_dir(farm_id) / "review-package.json"
        if not path.exists():
            raise FileNotFoundError(f"Review package is not ready for farm: {farm_id}")
        return read_json(path)

    def worker_patch(self, farm_id: str, worker_id: str) -> tuple[str, bool]:
        _safe_id(worker_id, "worker id")
        result = self.farm_result(farm_id)
        worker = next(
            (item for item in result.get("workers", []) if item.get("id") == worker_id),
            None,
        )
        if worker is None:
            raise FileNotFoundError(f"Unknown worker: {worker_id}")
        raw_path = worker.get("patch_file")
        if not isinstance(raw_path, str) or not raw_path:
            raise FileNotFoundError(f"No patch is available for worker: {worker_id}")
        patch_path = Path(raw_path).resolve()
        ensure_inside(self.repo_root, patch_path)
        data = patch_path.read_bytes()
        truncated = len(data) > MAX_ARTIFACT_BYTES
        return data[:MAX_ARTIFACT_BYTES].decode("utf-8", errors="replace"), truncated

    def change_sets(self, farm_id: str) -> list[dict[str, Any]]:
        return self.change_controller.change_sets(self.farm_result(farm_id))

    def worker_change_set(self, farm_id: str, worker_id: str) -> dict[str, Any]:
        _safe_id(worker_id, "worker id")
        return build_change_set(self.repo_root, self.farm_result(farm_id), worker_id)

    def apply_candidate(self, farm_id: str, worker_id: str) -> dict[str, Any]:
        _safe_id(worker_id, "worker id")
        return self.change_controller.apply(self.farm_dir(farm_id), worker_id)

    def merge_candidate(self, farm_id: str) -> dict[str, Any]:
        return self.change_controller.merge(self.farm_dir(farm_id))

    def rollback_candidate(
        self,
        farm_id: str,
        checkpoint_id: str,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        return self.change_controller.rollback(
            self.farm_dir(farm_id),
            checkpoint_id,
            force=force,
        )

    def decide(self, farm_id: str, raw_decision: dict[str, Any]) -> dict[str, Any]:
        farm_dir = self.farm_dir(farm_id)
        decision = SupervisorDecision.from_dict(raw_decision)
        if decision.task_id != farm_id:
            raise ValueError("Decision task_id must match the selected farm.")
        input_file = farm_dir / "supervisor-decision.input.json"
        write_json(input_file, decision.to_json())
        result = record_supervisor_decision(farm_dir, input_file)
        linked = self.threads.find_by_farm(farm_id)
        if linked:
            thread_id, turn_id = linked
            self.threads.add_item(
                thread_id,
                turn_id,
                "supervisor_decision",
                status="completed",
                payload={"decision": decision.to_json()},
            )
            status = "awaiting_user" if decision.decision == "hold_for_user" else "completed"
            self.threads.update_turn(thread_id, turn_id, status)
        return result

    def create_thread(self, payload: dict[str, Any]) -> dict[str, Any]:
        unknown = sorted(set(payload) - {"title"})
        if unknown:
            raise ValueError("Unknown thread fields: " + ", ".join(unknown))
        title = payload.get("title", "New task")
        if not isinstance(title, str):
            raise ValueError("title must be a string.")
        return self.threads.create(title)

    def bootstrap(self) -> dict[str, Any]:
        runtime = self.settings()["runtime"]
        supervisor_ready = bool(
            runtime["native_ready"]
            if self.config.agent_backend == "native"
            else runtime["codex_available"]
        )
        profiles: list[dict[str, Any]] = []
        names = sorted(self.config.worker_profiles)
        if not names:
            names = [self.config.default_worker_profile or "default"]
        for name in names:
            try:
                resolved, selected = resolve_worker_profile(
                    self.config,
                    None if name == "default" and not self.config.worker_profiles else name,
                )
            except (TypeError, ValueError):
                continue
            provider = resolved.worker_provider
            provider_data = self.config.model_providers.get(provider or "", {})
            raw_profile = self.config.worker_profiles.get(name, {})
            display_name = (
                raw_profile.get("display_name")
                if isinstance(raw_profile, dict)
                else None
            )
            profiles.append(
                {
                    "name": selected or name,
                    "display_name": display_name or selected or name,
                    "model": resolved.worker_model or "Model required",
                    "provider": provider or (resolved.worker_local_provider or "openai"),
                    "provider_name": provider_data.get("name") if isinstance(provider_data, dict) else None,
                    "timeout_seconds": resolved.timeout_seconds,
                    "is_default": (selected or name) == self.config.default_worker_profile,
                }
            )
        return {
            "app": {"name": "Agent Farm", "version": __version__},
            "repository": {
                "name": self.repo_root.name,
                "path": str(self.repo_root),
                "branch": _branch_name(self.repo_root),
            },
            "limits": {
                "max_parallel_workers": self.config.max_parallel_workers,
                "max_changed_files": self.config.max_changed_files,
                "max_diff_lines": self.config.max_diff_lines,
            },
            "defaults": {
                "profile": self.config.default_worker_profile,
                "allowed_paths": list(self.config.allowed_paths),
                "forbidden_paths": list(self.config.forbidden_paths),
                "test_commands": list(self.config.test_commands),
            },
            "supervisor": {
                "model": self.config.supervisor_model or self.config.worker_model or "Model required",
                "provider": self.config.supervisor_provider or self.config.worker_provider or "openai",
                "profile": self.config.supervisor_codex_profile,
                "timeout_seconds": self.config.supervisor_timeout_seconds,
                "mode": f"{self.config.agent_backend} read-only planner",
                "backend": self.config.agent_backend,
                "ready": supervisor_ready,
            },
            "profiles": profiles,
            "threads": self.threads.list(),
            "farms": self.list_farms(),
            "jobs": self.jobs.recent(),
            "plan_jobs": self.plan_jobs.recent(),
            "recovery": self.crash_recovery.report,
        }

    def close(self) -> None:
        self.approvals.close()
        self.jobs.close()
        self.plan_jobs.close()
        self.attachments.close()
        self.retention.maintain()
        self.logger.log("runtime.stopped", session_id=self.crash_recovery.session_id)
        self.crash_recovery.mark_clean_shutdown()


def _farm_summary(result: dict[str, Any]) -> dict[str, Any]:
    workers = result.get("workers") or []
    passed = sum(
        1 for worker in workers if worker.get("machine_review", {}).get("status") == "passed"
    )
    return {
        "farm_id": result.get("farm_id"),
        "plan_task_id": result.get("plan_task_id"),
        "status": result.get("status"),
        "base_ref": result.get("base_ref"),
        "base_commit": result.get("base_commit"),
        "worker_count": result.get("worker_count", len(workers)),
        "passed_workers": passed,
        "decision": result.get("decision"),
    }


def _static_assets() -> dict[str, tuple[str, bytes]]:
    root = files("agent_farm").joinpath("web")
    mapping = {
        "/": ("text/html; charset=utf-8", "index.html"),
        "/index.html": ("text/html; charset=utf-8", "index.html"),
        "/styles.css": ("text/css; charset=utf-8", "styles.css"),
        "/app.js": ("text/javascript; charset=utf-8", "app.js"),
    }
    return {
        path: (content_type, root.joinpath(filename).read_bytes())
        for path, (content_type, filename) in mapping.items()
    }


class ConsoleHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        state: ConsoleState,
        *,
        serve_assets: bool = True,
        stop_callback: Callable[[], None] | None = None,
    ) -> None:
        self.state = state
        self.assets = _static_assets() if serve_assets else {}
        self.stop_callback = stop_callback
        super().__init__(address, ConsoleRequestHandler)

    def handle_error(self, request: object, client_address: object) -> None:
        if isinstance(
            sys.exception(),
            (BrokenPipeError, ConnectionResetError, ConnectionAbortedError),
        ):
            return
        super().handle_error(request, client_address)

    def request_stop(self) -> None:
        if self.stop_callback is not None:
            self.stop_callback()
            return
        threading.Thread(target=self.shutdown, name="agent-farm-http-stop", daemon=True).start()


class ConsoleRequestHandler(BaseHTTPRequestHandler):
    server: ConsoleHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        self.server.state.logger.log(
            "http.access",
            correlation_id=getattr(self, "_correlation_id", None),
            client=self.client_address[0],
            message=format % args,
        )

    def _begin_request(self, method: str) -> None:
        self._correlation_id = valid_correlation_id(self.headers.get("X-Correlation-ID"))
        self._request_started = time.monotonic()
        self.server.state.logger.log(
            "http.request",
            correlation_id=self._correlation_id,
            method=method,
            path=urlsplit(self.path).path,
        )

    def do_GET(self) -> None:  # noqa: N802
        self._begin_request("GET")
        try:
            path = unquote(urlsplit(self.path).path)
            if path in self.server.assets:
                content_type, body = self.server.assets[path]
                self._send_bytes(HTTPStatus.OK, body, content_type)
                return
            if path == "/api/health":
                self._send_json(HTTPStatus.OK, self.server.state.health())
                return
            if path == "/api/protocol":
                self._send_json(HTTPStatus.OK, protocol_descriptor())
                return
            if path == "/api/protocol/schemas":
                self._send_json(HTTPStatus.OK, protocol_descriptor(include_schemas=True))
                return
            if path == "/api/bootstrap":
                self._send_json(HTTPStatus.OK, self.server.state.bootstrap())
                return
            if path == "/api/farms":
                self._send_json(HTTPStatus.OK, {"farms": self.server.state.list_farms()})
                return
            if path == "/api/checkpoints":
                self._send_json(
                    HTTPStatus.OK,
                    {"checkpoints": self.server.state.checkpoints.list()},
                )
                return
            if path == "/api/settings":
                self._send_json(HTTPStatus.OK, self.server.state.settings())
                return
            if path == "/api/pricing":
                self._send_json(
                    HTTPStatus.OK,
                    price_catalog(self.server.state.config.model_price_overrides),
                )
                return
            if path == "/api/threads":
                self._send_json(HTTPStatus.OK, {"threads": self.server.state.threads.list()})
                return
            if path == "/api/approvals":
                query = parse_qs(urlsplit(self.path).query)
                status = query.get("status", [None])[0]
                if status not in {None, "pending", "resolved"}:
                    raise ValueError("status must be pending or resolved.")
                self._send_json(
                    HTTPStatus.OK,
                    {"approvals": self.server.state.approvals.list(status=status)},
                )
                return
            parts = [part for part in path.split("/") if part]
            if len(parts) == 3 and parts[:2] == ["api", "approvals"]:
                self._send_json(HTTPStatus.OK, self.server.state.approvals.get(parts[2]))
                return
            if len(parts) == 3 and parts[:2] == ["api", "threads"]:
                self._send_json(HTTPStatus.OK, self.server.state.threads.read(parts[2]))
                return
            if len(parts) == 4 and parts[:2] == ["api", "providers"] and parts[3] == "models":
                query = parse_qs(urlsplit(self.path).query)
                refresh = query.get("refresh", ["0"])[0] in {"1", "true", "yes"}
                self._send_json(
                    HTTPStatus.OK,
                    self.server.state.provider_models(parts[2], refresh=refresh),
                )
                return
            if len(parts) == 4 and parts[:2] == ["api", "threads"] and parts[3] == "events":
                query = parse_qs(urlsplit(self.path).query)
                raw_after = query.get("after", ["0"])[0]
                try:
                    after = int(raw_after)
                except ValueError as exc:
                    raise ValueError("after must be an integer.") from exc
                self._send_json(
                    HTTPStatus.OK,
                    {"events": self.server.state.threads.events(parts[2], after=after)},
                )
                return
            if len(parts) == 3 and parts[:2] == ["api", "jobs"]:
                self._send_json(HTTPStatus.OK, self.server.state.jobs.get(parts[2]))
                return
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "events":
                query = parse_qs(urlsplit(self.path).query)
                try:
                    after = int(query.get("after", ["0"])[0])
                except ValueError as exc:
                    raise ValueError("after must be an integer.") from exc
                self._send_json(
                    HTTPStatus.OK,
                    self.server.state.jobs.events(parts[2], after=after),
                )
                return
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "stream":
                self._stream_job_events(self.server.state.jobs, parts[2], "farm")
                return
            if len(parts) == 3 and parts[:2] == ["api", "plan-jobs"]:
                self._send_json(HTTPStatus.OK, self.server.state.plan_jobs.get(parts[2]))
                return
            if len(parts) == 4 and parts[:2] == ["api", "plan-jobs"] and parts[3] == "events":
                query = parse_qs(urlsplit(self.path).query)
                try:
                    after = int(query.get("after", ["0"])[0])
                except ValueError as exc:
                    raise ValueError("after must be an integer.") from exc
                self._send_json(
                    HTTPStatus.OK,
                    self.server.state.plan_jobs.events(parts[2], after=after),
                )
                return
            if (
                len(parts) == 4
                and parts[:2] == ["api", "plan-jobs"]
                and parts[3] == "stream"
            ):
                self._stream_job_events(self.server.state.plan_jobs, parts[2], "plan")
                return
            if len(parts) == 3 and parts[:2] == ["api", "farms"]:
                self._send_json(HTTPStatus.OK, self.server.state.farm_result(parts[2]))
                return
            if len(parts) == 4 and parts[:2] == ["api", "farms"] and parts[3] == "review-package":
                self._send_json(HTTPStatus.OK, self.server.state.review_package(parts[2]))
                return
            if len(parts) == 4 and parts[:2] == ["api", "farms"] and parts[3] == "changesets":
                self._send_json(
                    HTTPStatus.OK,
                    {"change_sets": self.server.state.change_sets(parts[2])},
                )
                return
            if len(parts) == 4 and parts[:2] == ["api", "farms"] and parts[3] == "checkpoints":
                self._send_json(
                    HTTPStatus.OK,
                    {"checkpoints": self.server.state.checkpoints.list(farm_id=parts[2])},
                )
                return
            if (
                len(parts) == 6
                and parts[:2] == ["api", "farms"]
                and parts[3] == "workers"
                and parts[5] == "changeset"
            ):
                self._send_json(
                    HTTPStatus.OK,
                    self.server.state.worker_change_set(parts[2], parts[4]),
                )
                return
            if (
                len(parts) == 6
                and parts[:2] == ["api", "farms"]
                and parts[3] == "workers"
                and parts[5] == "patch"
            ):
                patch, truncated = self.server.state.worker_patch(parts[2], parts[4])
                self._send_json(HTTPStatus.OK, {"patch": patch, "truncated": truncated})
                return
            self._send_error(HTTPStatus.NOT_FOUND, "Endpoint not found.")
        except Exception as exc:
            self._handle_exception(exc)

    def do_POST(self) -> None:  # noqa: N802
        self._begin_request("POST")
        try:
            self._check_origin()
            path = unquote(urlsplit(self.path).path)
            payload = self._read_json()
            if path == "/api/runtime/stop":
                if payload:
                    raise ValueError("Runtime stop does not accept request fields.")
                self._send_json(HTTPStatus.ACCEPTED, {"status": "stopping"})
                self.server.request_stop()
                return
            if path == "/api/diagnostics/export":
                if payload:
                    raise ValueError("Diagnostic export does not accept request fields.")
                self._send_json(HTTPStatus.CREATED, self.server.state.export_diagnostics())
                return
            if path == "/api/protocol/initialize":
                self._send_json(
                    HTTPStatus.OK,
                    self.server.state.initialize_protocol(payload),
                )
                return
            if path == "/api/attachments":
                unknown = sorted(set(payload) - {"local_path"})
                if unknown:
                    raise ValueError("Unknown attachment fields: " + ", ".join(unknown))
                attachment = self.server.state.attachments.add(payload.get("local_path"))
                self._send_json(HTTPStatus.CREATED, attachment.public_json())
                return
            if path == "/api/farms":
                if "plan" in payload:
                    unknown = sorted(
                        set(payload) - {"plan", "thread_id", "turn_id", "attachments"}
                    )
                    if unknown:
                        raise ValueError("Unknown Farm submission fields: " + ", ".join(unknown))
                    raw_plan = payload.get("plan")
                    if not isinstance(raw_plan, dict):
                        raise ValueError("plan must be a JSON object.")
                    job = self.server.state.jobs.submit(
                        raw_plan,
                        thread_id=payload.get("thread_id"),
                        turn_id=payload.get("turn_id"),
                        attachment_ids=payload.get("attachments"),
                        correlation_id=self._correlation_id,
                    )
                else:
                    job = self.server.state.jobs.submit(
                        payload, correlation_id=self._correlation_id
                    )
                self._send_json(HTTPStatus.ACCEPTED, job)
                return
            if path == "/api/plans":
                job = self.server.state.plan_jobs.submit(
                    payload, correlation_id=self._correlation_id
                )
                self._send_json(HTTPStatus.ACCEPTED, job)
                return
            if path == "/api/settings":
                self._send_json(HTTPStatus.OK, self.server.state.save_settings(payload))
                return
            if path == "/api/threads":
                self._send_json(HTTPStatus.CREATED, self.server.state.create_thread(payload))
                return
            parts = [part for part in path.split("/") if part]
            if len(parts) == 4 and parts[:2] == ["api", "threads"]:
                thread_id, action = parts[2], parts[3]
                if action == "rename":
                    self._send_json(
                        HTTPStatus.OK,
                        self.server.state.threads.rename(thread_id, str(payload.get("title", ""))),
                    )
                    return
                if action in {"archive", "resume"}:
                    self._send_json(
                        HTTPStatus.OK,
                        self.server.state.threads.archive(thread_id, archived=action == "archive"),
                    )
                    return
                if action == "fork":
                    self._send_json(
                        HTTPStatus.CREATED,
                        self.server.state.threads.fork(thread_id, turn_id=payload.get("turn_id")),
                    )
                    return
                if action == "delete":
                    if payload:
                        raise ValueError("Thread deletion does not accept request fields.")
                    self.server.state.threads.delete(thread_id)
                    self._send_json(HTTPStatus.OK, {"deleted": True, "thread_id": thread_id})
                    return
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "cancel":
                if payload:
                    raise ValueError("Farm cancellation does not accept request fields.")
                self._send_json(
                    HTTPStatus.ACCEPTED,
                    self.server.state.jobs.cancel(parts[2]),
                )
                return
            if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "retry":
                unknown = sorted(set(payload) - {"worker_id"})
                if unknown:
                    raise ValueError("Unknown retry fields: " + ", ".join(unknown))
                worker_id = payload.get("worker_id")
                if worker_id is not None and not isinstance(worker_id, str):
                    raise ValueError("worker_id must be a string or null.")
                self._send_json(
                    HTTPStatus.ACCEPTED,
                    self.server.state.jobs.retry(parts[2], worker_id=worker_id),
                )
                return
            if (
                len(parts) == 6
                and parts[:2] == ["api", "jobs"]
                and parts[3] == "workers"
                and parts[5] == "cancel"
            ):
                if payload:
                    raise ValueError("Worker cancellation does not accept request fields.")
                self._send_json(
                    HTTPStatus.ACCEPTED,
                    self.server.state.jobs.cancel(parts[2], worker_id=parts[4]),
                )
                return
            if (
                len(parts) == 4
                and parts[:2] == ["api", "plan-jobs"]
                and parts[3] == "cancel"
            ):
                if payload:
                    raise ValueError("Planning cancellation does not accept request fields.")
                self._send_json(
                    HTTPStatus.ACCEPTED,
                    self.server.state.plan_jobs.cancel(parts[2]),
                )
                return
            if (
                len(parts) == 4
                and parts[:2] == ["api", "approvals"]
                and parts[3] == "decision"
            ):
                unknown = sorted(set(payload) - {"decision"})
                if unknown:
                    raise ValueError("Unknown approval fields: " + ", ".join(unknown))
                decision = payload.get("decision")
                if not isinstance(decision, str):
                    raise ValueError("decision must be a string.")
                resolved = self.server.state.approvals.respond(parts[2], decision)
                if decision == "cancel":
                    if resolved["job_kind"] == "farm":
                        self.server.state.jobs.cancel(str(resolved["job_id"]))
                    elif resolved["job_kind"] == "plan":
                        self.server.state.plan_jobs.cancel(str(resolved["job_id"]))
                self._send_json(HTTPStatus.OK, resolved)
                return
            if len(parts) == 4 and parts[:2] == ["api", "farms"] and parts[3] == "decision":
                result = self.server.state.decide(parts[2], payload)
                self._send_json(HTTPStatus.OK, result)
                return
            if len(parts) == 4 and parts[:2] == ["api", "farms"] and parts[3] == "apply":
                unknown = sorted(set(payload) - {"worker_id"})
                if unknown:
                    raise ValueError("Unknown apply fields: " + ", ".join(unknown))
                worker_id = payload.get("worker_id")
                if not isinstance(worker_id, str) or not worker_id:
                    raise ValueError("worker_id must be a non-empty string.")
                self._send_json(
                    HTTPStatus.OK,
                    self.server.state.apply_candidate(parts[2], worker_id),
                )
                return
            if len(parts) == 4 and parts[:2] == ["api", "farms"] and parts[3] == "merge":
                if payload:
                    raise ValueError("Merge does not accept request fields.")
                self._send_json(
                    HTTPStatus.OK,
                    self.server.state.merge_candidate(parts[2]),
                )
                return
            if len(parts) == 4 and parts[:2] == ["api", "farms"] and parts[3] == "rollback":
                unknown = sorted(set(payload) - {"checkpoint_id", "force"})
                if unknown:
                    raise ValueError("Unknown rollback fields: " + ", ".join(unknown))
                checkpoint_id = payload.get("checkpoint_id")
                force = payload.get("force", False)
                if not isinstance(checkpoint_id, str) or not checkpoint_id:
                    raise ValueError("checkpoint_id must be a non-empty string.")
                if type(force) is not bool:
                    raise ValueError("force must be boolean.")
                self._send_json(
                    HTTPStatus.OK,
                    self.server.state.rollback_candidate(
                        parts[2], checkpoint_id, force=force
                    ),
                )
                return
            self._send_error(HTTPStatus.NOT_FOUND, "Endpoint not found.")
        except Exception as exc:
            self._handle_exception(exc)

    def do_DELETE(self) -> None:  # noqa: N802
        self._begin_request("DELETE")
        try:
            self._check_origin()
            path = unquote(urlsplit(self.path).path)
            parts = [part for part in path.split("/") if part]
            if len(parts) == 3 and parts[:2] == ["api", "attachments"]:
                removed = self.server.state.attachments.remove(parts[2])
                if not removed:
                    raise FileNotFoundError("Unknown attachment.")
                self._send_json(HTTPStatus.OK, {"removed": True})
                return
            self._send_error(HTTPStatus.NOT_FOUND, "Endpoint not found.")
        except Exception as exc:
            self._handle_exception(exc)

    def _check_origin(self) -> None:
        origin = self.headers.get("Origin")
        if not origin:
            return
        parsed = urlsplit(origin)
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise PermissionError("Cross-origin requests are not allowed.")

    def _stream_job_events(self, registry: Any, job_id: str, stream_kind: str) -> None:
        query = parse_qs(urlsplit(self.path).query)
        try:
            after = int(query.get("after", ["0"])[0])
        except ValueError as exc:
            raise ValueError("after must be an integer.") from exc
        if after < 0:
            raise ValueError("after must be zero or greater.")

        # Validate the job before committing a streaming response status.
        registry.get(job_id)
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Correlation-ID", self._correlation_id)
        self.end_headers()
        self.close_connection = True

        try:
            while True:
                generation = registry.generation()
                batch = registry.events(job_id, after=after)
                for event in batch["events"]:
                    after = int(event["sequence"])
                    envelope = {
                        "protocol_version": RUNTIME_PROTOCOL_VERSION,
                        "stream": stream_kind,
                        "job_id": job_id,
                        "sequence": after,
                        "event": event,
                    }
                    body = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
                    frame = f"id: {after}\nevent: {event['type']}\ndata: {body}\n\n"
                    self.wfile.write(frame.encode("utf-8"))
                self.wfile.flush()

                if batch["status"] in TERMINAL_JOB_STATUSES:
                    return
                changed = registry.wait_for_change(generation, timeout=15)
                if not changed:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("Content-Length is required.")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length.") from exc
        if length < 1 or length > MAX_JSON_BODY:
            raise ValueError("JSON body must be between 1 byte and 1 MB.")
        content_type = self.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            raise ValueError("Content-Type must be application/json.")
        loaded = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("JSON body must be an object.")
        return loaded

    def _handle_exception(self, exc: Exception) -> None:
        if isinstance(exc, FileNotFoundError):
            status = HTTPStatus.NOT_FOUND
        elif isinstance(exc, PermissionError):
            status = HTTPStatus.FORBIDDEN
        elif isinstance(exc, ProtocolNegotiationError):
            status = HTTPStatus.CONFLICT
        elif isinstance(exc, (ValueError, WebConsoleError, json.JSONDecodeError)):
            status = HTTPStatus.BAD_REQUEST
        else:
            status = HTTPStatus.INTERNAL_SERVER_ERROR
        self.server.state.logger.log(
            "http.error",
            level="ERROR" if status.value >= 500 else "WARNING",
            correlation_id=getattr(self, "_correlation_id", None),
            method=self.command,
            path=urlsplit(self.path).path,
            status=status.value,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        self._send_error(status, str(exc) or status.phrase)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json(status, {"error": {"status": status.value, "message": message}})

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_bytes(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Correlation-ID", self._correlation_id)
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)


def serve_console(
    *,
    repo: Path,
    config_path: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise WebConsoleError("The Web console only supports loopback addresses.")
    if not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    repo_root = find_repo_root(repo)
    state = ConsoleState(repo_root=repo_root, config_path=config_path)
    server = ConsoleHTTPServer((host, port), state)
    actual_port = server.server_address[1]
    display_host = f"[{host}]" if ":" in host else host
    url = f"http://{display_host}:{actual_port}/"
    print(f"Agent Farm console: {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.2, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        state.close()
