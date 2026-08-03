from __future__ import annotations

from dataclasses import replace
from typing import Any

from .config import resolve_worker_profile
from .models import AgentFarmConfig
from .plans import WorkerPlan, WorkerPlanItem
from .usage import estimate_cost_usd


class RoutingError(RuntimeError):
    pass


COMPLEXITY_RANK = {"simple": 1, "standard": 2, "complex": 3}
CAPABILITY_RANK = {"economy": 1, "standard": 2, "premium": 3}
REFERENCE_USAGE = {
    "input_tokens": 100_000,
    "cached_input_tokens": 0,
    "cache_write_input_tokens": 0,
    "output_tokens": 20_000,
    "total_tokens": 120_000,
}


def _profile_candidate(
    config: AgentFarmConfig,
    name: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    resolved, _ = resolve_worker_profile(config, name)
    provider = resolved.worker_provider or "openai"
    model = resolved.worker_model or ""
    cost, pattern, _ = estimate_cost_usd(
        provider,
        model,
        REFERENCE_USAGE,
        config.model_price_overrides,
    )
    tier = str(raw.get("capability_tier") or "standard")
    return {
        "profile": name,
        "provider": provider,
        "model": model,
        "capability_tier": tier,
        "capability_rank": CAPABILITY_RANK[tier],
        "reference_cost_usd": cost,
        "price_pattern": pattern,
    }


def route_worker_plan(
    config: AgentFarmConfig,
    plan: WorkerPlan,
) -> tuple[WorkerPlan, list[dict[str, Any]]]:
    candidates = [
        _profile_candidate(config, name, raw)
        for name, raw in sorted(config.worker_profiles.items())
        if isinstance(raw, dict)
    ]
    if not candidates:
        return plan, []

    routed: list[WorkerPlanItem] = []
    decisions: list[dict[str, Any]] = []
    for worker in plan.workers:
        required = COMPLEXITY_RANK[worker.complexity]
        eligible = [item for item in candidates if item["capability_rank"] >= required]
        if not eligible:
            raise RoutingError(
                f"Worker '{worker.worker_id}' is {worker.complexity}, but no configured route "
                "meets its capability requirement."
            )
        priced = [item for item in eligible if item["reference_cost_usd"] is not None]
        if priced:
            selected = min(
                priced,
                key=lambda item: (
                    item["reference_cost_usd"],
                    item["capability_rank"],
                    item["profile"],
                ),
            )
            reason = "least_expensive_capable_priced_route"
        else:
            requested = next(
                (item for item in eligible if item["profile"] == worker.profile),
                None,
            )
            selected = requested or min(
                eligible, key=lambda item: (item["capability_rank"], item["profile"])
            )
            reason = "requested_capable_route_price_unknown"
        routed.append(replace(worker, profile=selected["profile"]))
        decisions.append(
            {
                "worker_id": worker.worker_id,
                "complexity": worker.complexity,
                "requested_profile": worker.profile,
                "selected_profile": selected["profile"],
                "provider": selected["provider"],
                "model": selected["model"],
                "capability_tier": selected["capability_tier"],
                "reference_cost_usd": selected["reference_cost_usd"],
                "price_pattern": selected["price_pattern"],
                "reason": reason,
            }
        )
    return replace(plan, workers=routed), decisions


def escalation_profiles(
    config: AgentFarmConfig,
    current_profile: str,
) -> list[str]:
    raw_current = config.worker_profiles.get(current_profile)
    if not isinstance(raw_current, dict):
        return []
    current_tier = str(raw_current.get("capability_tier") or "standard")
    current_rank = CAPABILITY_RANK[current_tier]
    explicit: list[str] = []
    escalation = raw_current.get("escalation_profile")
    if isinstance(escalation, str) and escalation:
        explicit.append(escalation)
    explicit.extend(
        item
        for item in raw_current.get("fallback_profiles") or []
        if isinstance(item, str)
    )
    automatic = [
        name
        for name, raw in config.worker_profiles.items()
        if name != current_profile
        and isinstance(raw, dict)
        and CAPABILITY_RANK[str(raw.get("capability_tier") or "standard")] > current_rank
    ]
    ordered: list[str] = []
    for name in explicit + automatic:
        if name not in ordered and name != current_profile:
            ordered.append(name)
    candidates = {
        name: _profile_candidate(config, name, config.worker_profiles[name])
        for name in ordered
    }
    explicit_order = {name: index for index, name in enumerate(explicit)}
    return sorted(
        ordered,
        key=lambda name: (
            0 if name in explicit_order else 1,
            explicit_order.get(name, 0),
            candidates[name]["reference_cost_usd"] is None,
            candidates[name]["reference_cost_usd"] or 0,
            candidates[name]["capability_rank"],
            name,
        ),
    )
