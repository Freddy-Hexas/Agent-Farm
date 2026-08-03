from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .util import ensure_inside
from .usage import estimate_cost_usd


def _empty_bucket() -> dict[str, Any]:
    return {
        "request_count": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "retry_count": 0,
        "latency_ms": 0.0,
        "estimated_cost_usd": 0.0,
        "unpriced_requests": 0,
    }


def _add(bucket: dict[str, Any], event: dict[str, Any]) -> None:
    usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
    bucket["request_count"] += 1
    for key in ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens"):
        value = usage.get(key)
        if type(value) is int and value >= 0:
            bucket[key] += value
    retries = event.get("retry_count")
    if type(retries) is int and retries >= 0:
        bucket["retry_count"] += retries
    latency = event.get("latency_ms")
    if isinstance(latency, (int, float)) and not isinstance(latency, bool) and latency >= 0:
        bucket["latency_ms"] += float(latency)
    cost = usage.get("estimated_cost_usd")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0:
        bucket["estimated_cost_usd"] += float(cost)
    else:
        bucket["unpriced_requests"] += 1


def _finish(bucket: dict[str, Any]) -> dict[str, Any]:
    result = dict(bucket)
    count = result["request_count"]
    result["average_latency_ms"] = round(result.pop("latency_ms") / count, 3) if count else 0.0
    result["estimated_cost_usd"] = round(result["estimated_cost_usd"], 9)
    return result


def _event_paths(repo_root: Path, farm_dir: Path, result: dict[str, Any]) -> list[Path]:
    candidates = [
        farm_dir / "supervisor-review-events.jsonl",
        farm_dir / "supervisor-synthesis-events.jsonl",
    ]
    plan_task_id = result.get("plan_task_id")
    if isinstance(plan_task_id, str) and plan_task_id:
        candidates.append(
            repo_root / ".agent-farm" / "supervisor" / plan_task_id / "supervisor-events.jsonl"
        )
    for worker in result.get("workers") or []:
        if not isinstance(worker, dict):
            continue
        raw_run_dir = worker.get("run_dir")
        if isinstance(raw_run_dir, str) and raw_run_dir:
            candidates.append(Path(raw_run_dir) / "worker-events.jsonl")

    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            ensure_inside(repo_root, resolved)
        except (OSError, ValueError):
            continue
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def farm_usage_report(
    repo_root: Path,
    farm_dir: Path,
    result: dict[str, Any],
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    farm_dir = farm_dir.resolve()
    ensure_inside(repo_root, farm_dir)
    supervisor = _empty_bucket()
    workers = _empty_bucket()
    total = _empty_bucket()
    by_agent: dict[str, dict[str, Any]] = {}
    by_model: dict[str, dict[str, Any]] = {}
    requests: list[dict[str, Any]] = []
    seen_requests: set[str] = set()

    for path in _event_paths(repo_root, farm_dir, result):
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("type") != "model.request.completed":
                continue
            request_id = event.get("request_id")
            if not isinstance(request_id, str) or not request_id or request_id in seen_requests:
                continue
            seen_requests.add(request_id)
            kind = "supervisor" if event.get("agent_kind") == "supervisor" else "worker"
            agent_id = str(event.get("agent_id") or kind)
            provider = str(event.get("provider") or "unknown")
            model = str(event.get("model") or "unknown")
            _add(supervisor if kind == "supervisor" else workers, event)
            _add(total, event)
            _add(by_agent.setdefault(agent_id, _empty_bucket()), event)
            _add(by_model.setdefault(f"{provider}/{model}", _empty_bucket()), event)
            requests.append(
                {
                    "request_id": request_id,
                    "agent_kind": kind,
                    "agent_id": agent_id,
                    "provider": provider,
                    "model": model,
                    "latency_ms": event.get("latency_ms"),
                    "retry_count": event.get("retry_count", 0),
                    "usage": event.get("usage") or {},
                }
            )

    finished_total = _finish(total)
    accepted_artifacts = 0
    if result.get("status") in {"COMPLETED", "SUPERVISOR_APPROVED", "VERIFIED", "MERGED"}:
        accepted_artifacts = 1
    elif (result.get("decision") or {}).get("decision") == "approve_merge":
        accepted_artifacts = 1

    supervisor_routes: list[tuple[float, str, str]] = []
    reference_usage = {
        "input_tokens": 100_000,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 20_000,
        "total_tokens": 120_000,
    }
    for request in requests:
        if request["agent_kind"] != "supervisor":
            continue
        reference_cost, _, _ = estimate_cost_usd(
            request["provider"], request["model"], reference_usage
        )
        if reference_cost is not None:
            supervisor_routes.append(
                (reference_cost, request["provider"], request["model"])
            )
    premium_route = max(supervisor_routes, default=None)
    premium_baseline: float | None = 0.0 if premium_route else None
    if premium_route is not None:
        for request in requests:
            request_usage = request.get("usage") if isinstance(request.get("usage"), dict) else {}
            request_cost, _, _ = estimate_cost_usd(
                premium_route[1], premium_route[2], request_usage
            )
            if request_cost is None:
                premium_baseline = None
                break
            premium_baseline = float(premium_baseline or 0) + request_cost
    fully_priced = finished_total["unpriced_requests"] == 0
    actual_cost = finished_total["estimated_cost_usd"] if fully_priced else None
    cost_per_artifact = (
        round(float(actual_cost) / accepted_artifacts, 9)
        if actual_cost is not None and accepted_artifacts > 0
        else None
    )
    savings = (
        round(float(premium_baseline) - float(actual_cost), 9)
        if premium_baseline is not None and actual_cost is not None
        else None
    )
    savings_percent = (
        round(savings / premium_baseline * 100, 3)
        if savings is not None and premium_baseline and premium_baseline > 0
        else None
    )
    return {
        "schema_version": 1,
        "farm_id": result.get("farm_id"),
        "supervisor": _finish(supervisor),
        "workers": _finish(workers),
        "total": finished_total,
        "by_agent": {key: _finish(value) for key, value in sorted(by_agent.items())},
        "by_model": {key: _finish(value) for key, value in sorted(by_model.items())},
        "requests": requests,
        "economics": {
            "accepted_artifact_count": accepted_artifacts,
            "cost_per_accepted_artifact_usd": cost_per_artifact,
            "premium_route": (
                {"provider": premium_route[1], "model": premium_route[2]}
                if premium_route
                else None
            ),
            "premium_only_estimate_usd": (
                round(premium_baseline, 9) if premium_baseline is not None else None
            ),
            "estimated_savings_usd": savings,
            "estimated_savings_percent": savings_percent,
            "method": (
                "Reprice all observed Farm tokens at the most expensive priced Supervisor route; "
                "compare with recorded mixed-route cost. Token volume is held constant."
            ),
        },
    }
