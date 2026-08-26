"""Run one real Supervisor -> Worker task and write a bounded smoke artifact.

This script intentionally uses the configured providers and native runtime. It
does not replace model calls with mocks or bypass the TaskRuntime API.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_farm.web_server import ConsoleState


DEFAULT_REQUEST = """Run a minimal end-to-end runtime proof.

Use exactly one implementation Worker with `complexity` set to `simple` and no
test commands. The Worker must create only
`test-artifacts/real-workflow-smoke/worker-proof.md` containing a short summary
of the real TaskRuntime, farm, session-ledger, and cancellation path it
inspected. Set the Worker `allowed_paths` to exactly
`agent_farm/task_runtime.py`, `agent_farm/farm.py`,
`agent_farm/runtime_store.py`, and
`test-artifacts/real-workflow-smoke/worker-proof.md`. These paths are required
so the current workspace snapshot, including uncommitted runtime changes, is
the source the Worker inspects. The Worker may only write the artifact path.
Inspect only the three `agent_farm` files; after at most three `read_file`
calls, write the artifact and finish. Do not execute tests, do not call the web,
and do not modify any other path. The file and the Worker final summary are the
acceptance evidence.
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _wait_for_terminal(state: ConsoleState, job_id: str, deadline: float) -> dict[str, Any]:
    terminal = {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"}
    while True:
        job = state.tasks.get(job_id)
        if job.get("status") in terminal:
            return job
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Real smoke did not settle before the polling deadline; task={job_id} status={job.get('status')}"
            )
        time.sleep(1.0)


def _write_artifact(artifact_dir: Path, job: dict[str, Any], events: list[dict[str, Any]]) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    workers = result.get("workers") if isinstance(result.get("workers"), list) else []
    routes = [
        {
            "worker": worker.get("id"),
            "profile": worker.get("profile"),
            "route": worker.get("route_id"),
            "status": worker.get("status"),
            "review": (worker.get("machine_review") or {}).get("status"),
        }
        for worker in workers
        if isinstance(worker, dict)
    ]
    summary = {
        "generated_at": _utc_now(),
        "task_id": job.get("job_id"),
        "session_id": job.get("session_id"),
        "status": job.get("status"),
        "farm_status": result.get("status"),
        "farm_id": job.get("farm_id"),
        "source_base_commit": result.get("source_base_commit"),
        "workspace_snapshot": result.get("workspace_snapshot"),
        "worker_routes": routes,
        "event_count": len(events),
        "event_types": sorted({str(event.get("type") or "event") for event in events}),
        "terminal_error": job.get("error"),
    }
    proof_path = artifact_dir / "worker-proof.md"
    (artifact_dir / "result.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# Real Workflow Smoke",
        "",
        "This artifact was produced by `scripts/run_real_smoke.py` through the local Agent Farm daemon.",
        "The Supervisor and Worker used the configured live provider routes; no mock model, fake farm,"
        " or proxy runner was used.",
        "",
        f"- Generated: `{summary['generated_at']}`",
        f"- Task: `{summary['task_id']}`",
        f"- Session: `{summary['session_id']}`",
        f"- Task status: **{summary['status']}**",
        f"- Farm status: **{summary['farm_status']}**",
        f"- Farm: `{summary['farm_id']}`",
        f"- Event count: `{summary['event_count']}`",
        f"- Workspace snapshot: `{summary['workspace_snapshot']}`",
        "",
        "## Route Evidence",
        "",
    ]
    if routes:
        lines.extend(
            f"- `{item['worker']}`: `{item['profile']}` -> `{item['route']}`; "
            f"status `{item['status']}`, machine review `{item['review']}`"
            for item in routes
        )
    else:
        lines.append("- No Worker record was returned.")
    lines.extend(
        [
            "",
            "## Event Evidence",
            "",
            "The task event cursor was read back from SQLite after completion. The recorded stream includes:",
            "",
            *[f"- `{event_type}`" for event_type in summary["event_types"]],
            "",
            "The full runtime database and farm evidence remain under `.agent-farm/`; this README is a bounded,"
            " secret-free handoff artifact.",
        ]
    )
    (artifact_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if proof_path.is_file():
        # The implementation Worker owns this proof. The smoke harness records
        # its presence but never rewrites or synthesizes the Worker artifact.
        summary["worker_proof"] = str(proof_path)
        (artifact_dir / "result.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--artifact-dir", type=Path, default=None)
    parser.add_argument("--poll-timeout-seconds", type=float, default=3600)
    parser.add_argument("--request", default=DEFAULT_REQUEST)
    args = parser.parse_args()

    repo = args.repo.resolve()
    artifact_dir = (args.artifact_dir or repo / "test-artifacts" / "real-workflow-smoke").resolve()
    state = ConsoleState(repo, args.config.resolve() if args.config else None)
    try:
        submitted = state.tasks.submit(
            {
                "request": args.request,
                "worker_count": 1,
                "base_ref": "HEAD",
            }
        )
        job = _wait_for_terminal(
            state,
            submitted["job_id"],
            time.monotonic() + max(1.0, args.poll_timeout_seconds),
        )
        events = state.tasks.events(job["job_id"])["events"]
        _write_artifact(artifact_dir, job, events)
        print(json.dumps({"task_id": job["job_id"], "status": job["status"], "artifact_dir": str(artifact_dir)}, indent=2))
        return 0 if job["status"] == "COMPLETED" else 1
    finally:
        state.close()


if __name__ == "__main__":
    raise SystemExit(main())
