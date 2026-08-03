from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from .config import config_from_dict, load_config
from .native_agent import FINISH_TOOL, run_native_agent
from .plans import SupervisorDecision, WorkerPlan
from .specs import slugify
from .util import ensure_inside, run_command, write_json


class SupervisorError(RuntimeError):
    pass


def synthesize_farm_deliverable(
    *,
    repo_root: Path,
    farm_dir: Path,
    plan: WorkerPlan,
    config_path: Path | None = None,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    approval_callback: Callable[[dict[str, Any]], str] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    attachment_context: str = "",
    model_attachments: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Combine all passing Worker evidence into one user-visible artifact."""

    if plan.deliverable is None:
        raise SupervisorError("This Worker Plan has no collaborative deliverable.")
    repo_root = repo_root.resolve()
    farm_dir = farm_dir.resolve()
    ensure_inside(repo_root, farm_dir)
    review_file = farm_dir / "review-package.json"
    if not review_file.is_file():
        raise SupervisorError("The Farm review package is not ready.")
    review_package = json.loads(review_file.read_text(encoding="utf-8"))
    passing = [
        worker
        for worker in review_package.get("workers") or []
        if isinstance(worker, dict)
        and (worker.get("machine_review") or {}).get("status") == "passed"
    ]
    if len(passing) != len(plan.workers):
        raise SupervisorError(
            "Every planned Worker must pass machine review before collaborative synthesis."
        )

    config = load_config(repo_root, config_path)
    if config.agent_backend != "native":
        raise SupervisorError("Collaborative synthesis currently requires the native backend.")
    synthesis_data = config.to_json()
    synthesis_data["allowed_paths"] = [plan.deliverable.path]
    synthesis_data["test_commands"] = []
    synthesis_config = config_from_dict(synthesis_data)
    review_relative = review_file.relative_to(repo_root).as_posix()
    plan_relative = (farm_dir / "worker-plan.json").relative_to(repo_root).as_posix()

    evidence_lines: list[str] = []
    for worker in passing:
        evidence_lines.append(f"- Worker `{worker.get('id')}` ({worker.get('role')}):")
        for label, key in (("final report", "final_file"), ("patch", "patch_file")):
            raw_path = worker.get(key)
            if not isinstance(raw_path, str) or not raw_path:
                continue
            try:
                path = Path(raw_path).resolve()
                ensure_inside(repo_root, path)
                evidence_lines.append(
                    f"  - {label}: `{path.relative_to(repo_root).as_posix()}`"
                )
            except (OSError, ValueError):
                continue

    result = run_native_agent(
        config=synthesis_config,
        repo_root=repo_root,
        worktree=repo_root,
        prompt=f"""Create the final collaborative deliverable for Farm {review_package.get('task_id')}.

Read the validated Worker Plan at `{plan_relative}` and the machine-review package at
`{review_relative}`. Then read every passing Worker artifact listed below:
{chr(10).join(evidence_lines)}

Final deliverable path: `{plan.deliverable.path}`
Synthesis instructions:
{plan.deliverable.instructions}

User attachment context:
{attachment_context or "No user attachments were supplied."}

Requirements:
- Integrate material contributions from every Worker; do not merely choose one.
- Resolve disagreements conservatively and distinguish sourced facts from analysis.
- Preserve working source URLs as Markdown links when the Worker evidence provides them.
- Never invent a source, quote, date, number, test, or market fact.
- Write the complete final artifact with write_file at exactly the deliverable path.
- Read the written file back, check that it is coherent and non-empty, then call finish.
""",
        system_prompt=(
            "You are Agent Farm's high-capability synthesis Supervisor. Combine all validated "
            "Worker evidence into one polished user deliverable. You may write only the exact "
            "deliverable path. Do not modify intermediate Worker artifacts, repository code, "
            "configuration, secrets, git metadata, or permissions."
        ),
        provider=config.supervisor_provider or config.worker_provider,
        model=config.supervisor_model or config.worker_model,
        timeout_seconds=None,
        writable=True,
        events_file=farm_dir / "supervisor-synthesis-events.jsonl",
        terminal_tool=FINISH_TOOL,
        reasoning_mode=config.supervisor_reasoning_mode,
        reasoning_effort=config.supervisor_reasoning_effort,
        event_callback=event_callback,
        approval_callback=approval_callback,
        cancel_check=cancel_check,
        model_attachments=model_attachments,
        usage_context={
            "farm_id": str(review_package.get("task_id") or ""),
            "agent_id": "supervisor-synthesis",
            "agent_kind": "supervisor",
            "phase": "synthesis",
        },
    )
    if not result.ok:
        raise SupervisorError(result.error or "Native Supervisor synthesis failed.")
    output_path = (repo_root / plan.deliverable.path).resolve()
    ensure_inside(repo_root, output_path)
    if not output_path.is_file() or not output_path.read_text(encoding="utf-8").strip():
        raise SupervisorError("Supervisor finished without writing the final deliverable.")
    return {
        "path": str(output_path),
        "relative_path": plan.deliverable.path,
        "summary": result.final_text.strip(),
        "events_file": str(farm_dir / "supervisor-synthesis-events.jsonl"),
    }


WORKER_PLAN_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "task_id",
        "base_ref",
        "max_parallel",
        "workers",
        "deliverable",
    ],
    "properties": {
        "schema_version": {"type": "integer", "const": 1},
        "task_id": {"type": "string"},
        "base_ref": {"type": "string"},
        "max_parallel": {"type": "integer", "minimum": 1},
        "deliverable": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["path", "instructions"],
            "properties": {
                "path": {"type": "string", "minLength": 1},
                "instructions": {"type": "string", "minLength": 1},
            },
        },
        "workers": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id",
                    "role",
                    "profile",
                    "complexity",
                    "attachments",
                    "depends_on",
                    "goal",
                    "allowed_paths",
                    "forbidden_paths",
                    "test_commands",
                    "acceptance",
                    "context",
                ],
                "properties": {
                    "id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9._-]*$"},
                    "role": {"type": "string", "minLength": 1},
                    "profile": {"type": "string", "minLength": 1},
                    "complexity": {
                        "type": "string",
                        "enum": ["simple", "standard", "complex"],
                    },
                    "attachments": {"type": "array", "items": {"type": "string"}},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                    "goal": {"type": "string", "minLength": 1},
                    "allowed_paths": {"type": "array", "items": {"type": "string"}},
                    "forbidden_paths": {"type": "array", "items": {"type": "string"}},
                    "test_commands": {"type": "array", "items": {"type": "string"}},
                    "acceptance": {"type": "array", "items": {"type": "string"}},
                    "context": {"type": "string"},
                },
            },
        },
    },
}


SUPERVISOR_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "decision",
        "task_id",
        "approved_worker",
        "risk_level",
        "reason",
        "rollback_required",
    ],
    "properties": {
        "schema_version": {"type": "integer", "const": 1},
        "decision": {
            "type": "string",
            "enum": ["approve_merge", "request_revision", "reject", "hold_for_user"],
        },
        "task_id": {"type": "string"},
        "approved_worker": {"type": ["string", "null"]},
        "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
        "reason": {"type": "string", "minLength": 1},
        "rollback_required": {"type": "boolean"},
    },
}


def _planner_prompt(
    *,
    request: str,
    task_id: str,
    base_ref: str,
    worker_count: int,
    profiles: list[str],
    attachment_context: str = "",
) -> str:
    available = ", ".join(profiles)
    return f"""You are the expensive supervisor brain for Agent Farm.

Analyze the repository in read-only mode and translate the user's request into a precise,
safe Worker Plan. The workers run independently in isolated git worktrees. Give each worker a
useful standalone contribution. When the user asks for research, analysis, writing, or another
task whose Worker contributions must be combined, set deliverable to the final repository-relative
file path and precise synthesis instructions. Agent Farm will give every passing Worker artifact
to the expensive Supervisor, which writes that final deliverable. Set deliverable to null only when
the Workers are competing implementation alternatives and exactly one patch should be selected.
Reserve architecture choices, synthesis, final review, and merge authority for the supervisor.

User request:
{request}

User attachment context:
{attachment_context or "No user attachments were supplied."}

Planning constraints:
- Output only the JSON object required by the supplied schema.
- When native tools are available, inspect relevant repository files and call submit_worker_plan.
- task_id must be exactly: {task_id}
- base_ref must be exactly: {base_ref}
- Create at most {worker_count} workers and no more than are genuinely useful.
- Available worker profiles: {available}
- Follow the literal user request. Do not replace it with a follow-on task, UI, dashboard,
  visualization, or alternative deliverable merely because a similarly named artifact already
  exists in the repository. A pre-existing artifact is context, not authorization to change scope.
- If the user specifies a Worker count or named Worker roles, preserve that team structure exactly
  unless it would violate a safety boundary.
- If the user asks multiple Agents to research/analyze and then combine, summarize, or report the
  results, deliverable must be non-null and the Workers must perform those requested research and
  analysis roles. Do not turn them into competing implementation candidates.
- Convert a requested output folder that is the repository root into a descriptive relative file
  inside that repository, such as `market-report.md`; never emit an absolute deliverable path.
- Prefer the cheapest suitable profile. Use a stronger worker profile only where needed.
- Classify every Worker as simple, standard, or complex. Simple means bounded lookup, formatting,
  or a narrow low-risk edit; complex means architecture, difficult debugging, security-sensitive
  work, or broad reasoning; everything else is standard. The runtime enforces the least expensive
  configured route whose capability tier meets that classification.
- Assign only the exact attachment IDs a Worker needs. Use the IDs shown in attachment context;
  use an empty attachments array when the Worker does not need user files. Never broadcast every
  attachment to every Worker by default.
- Use depends_on to express real ordering constraints between Worker IDs. Keep it empty for
  independent work so those Workers remain parallel; never create cycles.
- Every worker must have a narrow allowed_paths allowlist grounded in this repository.
- For a collaborative deliverable, assign different intermediate paths to Workers and ensure the
  deliverable path is not one of those Worker paths.
- Include concrete acceptance checks and repository-appropriate test commands.
- test_commands are executed on Windows by Agent Farm and must be standalone commands accepted by
  the native command allowlist: read-only git; rg; pytest/ruff/mypy; `python -m` unittest, pytest,
  compileall, ruff, or mypy; normal npm/pnpm/yarn/bun test/build scripts; dotnet test/build/format;
  cargo test/check/clippy/fmt; or go test/vet/fmt. Never emit POSIX `test`, shell built-ins,
  `python -c`, pipes, redirects, or command chains. Use an empty array when the repository has no
  appropriate automated command, especially for a Markdown research brief.
- Do not assign merge, push, deployment, secrets, credential, or permission changes.
- Keep context concise; do not paste large source files or the entire conversation.
"""


def _request_requires_deliverable(request: str) -> bool:
    lowered = request.casefold()
    chinese_markers = ("汇总", "报告", "总结成", "整理成", "写一份")
    if any(marker in request for marker in chinese_markers):
        return True
    return bool(
        re.search(
            r"\b(write|create|produce|generate|compile|combine|aggregate|synthesi[sz]e)\b"
            r".{0,48}\b(report|brief|memo|document|deliverable)\b",
            lowered,
        )
    )


def draft_worker_plan(
    *,
    repo_root: Path,
    request: str,
    task_id: str | None = None,
    base_ref: str = "HEAD",
    worker_count: int = 3,
    config_path: Path | None = None,
    output_dir: Path | None = None,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    approval_callback: Callable[[dict[str, Any]], str] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    attachment_context: str = "",
    model_attachments: list[dict[str, str]] | None = None,
) -> WorkerPlan:
    request = request.strip()
    if not request:
        raise ValueError("Supervisor request must not be empty.")
    if type(worker_count) is not int or not 1 <= worker_count <= 12:
        raise ValueError("worker_count must be between 1 and 12.")

    repo_root = repo_root.resolve()
    config = load_config(repo_root, config_path)
    if (
        type(config.supervisor_timeout_seconds) is not int
        or config.supervisor_timeout_seconds < 1
    ):
        raise SupervisorError("supervisor_timeout_seconds must be a positive integer.")
    profiles = sorted(config.worker_profiles)
    if not profiles:
        profiles = [config.default_worker_profile or "default"]

    selected_task_id = slugify(task_id or request[:64], fallback="task")
    artifacts = (
        output_dir.resolve()
        if output_dir is not None
        else (repo_root / ".agent-farm" / "supervisor" / selected_task_id).resolve()
    )
    ensure_inside(repo_root, artifacts)
    artifacts.mkdir(parents=True, exist_ok=True)
    schema_file = artifacts / "worker-plan.schema.json"
    output_file = artifacts / "worker-plan.draft.json"
    stderr_file = artifacts / "supervisor-stderr.log"
    write_json(schema_file, WORKER_PLAN_SCHEMA)

    planner_prompt = _planner_prompt(
        request=request,
        task_id=selected_task_id,
        base_ref=base_ref,
        worker_count=worker_count,
        profiles=profiles,
        attachment_context=attachment_context,
    )
    if config.agent_backend == "native":
        submit_schema = {
            key: value for key, value in WORKER_PLAN_SCHEMA.items() if key != "$schema"
        }
        result = run_native_agent(
            config=config,
            repo_root=repo_root,
            worktree=repo_root,
            prompt=planner_prompt,
            system_prompt=(
                "You are Agent Farm's high-capability Supervisor. Inspect the repository using "
                "read-only tools, split the request into independent economical Worker tasks, and "
                "call submit_worker_plan. Never edit files, run commands, or expose secrets."
            ),
            provider=config.supervisor_provider or config.worker_provider,
            model=config.supervisor_model or config.worker_model,
            timeout_seconds=None,
            writable=False,
            events_file=artifacts / "supervisor-events.jsonl",
            terminal_tool={
                "name": "submit_worker_plan",
                "description": "Submit the final validated Worker Plan after repository inspection.",
                "parameters": submit_schema,
            },
            reasoning_mode=config.supervisor_reasoning_mode,
            reasoning_effort=config.supervisor_reasoning_effort,
            event_callback=event_callback,
            approval_callback=approval_callback,
            cancel_check=cancel_check,
            model_attachments=model_attachments,
            usage_context={
                "farm_id": selected_task_id,
                "agent_id": "supervisor-planner",
                "agent_kind": "supervisor",
                "phase": "planning",
            },
        )
        stderr_file.write_text(result.error or "", encoding="utf-8")
        if not result.ok:
            raise SupervisorError(result.error or "Native Supervisor failed.")
        raw_plan = result.terminal_payload
        if raw_plan is None:
            try:
                raw_plan = json.loads(result.final_text)
            except json.JSONDecodeError as exc:
                raise SupervisorError("Native Supervisor did not submit a Worker Plan.") from exc
        output_file.write_text(
            json.dumps(raw_plan, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
        )
    else:
        args = [
            config.codex_binary,
            "exec",
            "--cd",
            str(repo_root),
            "--sandbox",
            "read-only",
            "--ephemeral",
            "--output-schema",
            str(schema_file),
            "--output-last-message",
            str(output_file),
            "-c",
            'approval_policy="never"',
        ]
        if config.supervisor_codex_profile:
            args.extend(["--profile", config.supervisor_codex_profile])
        if config.supervisor_model:
            args.extend(["--model", config.supervisor_model])
        args.append("-")
        command_result = run_command(
            args,
            repo_root,
            timeout_seconds=None,
            input_text=planner_prompt,
        )
        stderr_file.write_text(command_result.stderr, encoding="utf-8")
        if not command_result.ok:
            message = (
                command_result.stderr.strip()
                or command_result.stdout.strip()
                or "Codex supervisor failed."
            )
            raise SupervisorError(message)
    if not output_file.exists():
        raise SupervisorError("Codex supervisor did not produce a Worker Plan.")
    try:
        raw = json.loads(output_file.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict) or "deliverable" not in raw:
            raise ValueError("Supervisor omitted the required deliverable field")
        plan = WorkerPlan.from_dict(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise SupervisorError(f"Supervisor returned an invalid Worker Plan: {exc}") from exc
    if plan.task_id != selected_task_id or plan.base_ref != base_ref:
        raise SupervisorError("Supervisor changed protected task_id or base_ref fields.")
    unknown_profiles = sorted({worker.profile for worker in plan.workers} - set(profiles))
    if unknown_profiles:
        raise SupervisorError(
            "Supervisor selected unknown worker profiles: " + ", ".join(unknown_profiles)
        )
    if len(plan.workers) > worker_count:
        raise SupervisorError("Supervisor exceeded the requested worker count.")
    if _request_requires_deliverable(request) and plan.deliverable is None:
        raise SupervisorError(
            "Supervisor changed a requested collaborative deliverable into candidate selection."
        )
    return plan


def draft_supervisor_decision(
    *,
    repo_root: Path,
    farm_dir: Path,
    config_path: Path | None = None,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    approval_callback: Callable[[dict[str, Any]], str] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> SupervisorDecision:
    repo_root = repo_root.resolve()
    farm_dir = farm_dir.resolve()
    ensure_inside(repo_root, farm_dir)
    review_file = farm_dir / "review-package.json"
    if not review_file.is_file():
        raise SupervisorError("The Farm review package is not ready.")
    review_package = json.loads(review_file.read_text(encoding="utf-8"))
    farm_id = review_package.get("task_id")
    if not isinstance(farm_id, str) or not farm_id:
        raise SupervisorError("The Farm review package has no task_id.")
    config = load_config(repo_root, config_path)
    if config.agent_backend != "native":
        raise SupervisorError("Automatic final review currently requires the native backend.")
    review_relative = review_file.relative_to(repo_root).as_posix()
    patch_paths: list[str] = []
    for worker in review_package.get("workers") or []:
        if not isinstance(worker, dict) or not isinstance(worker.get("patch_file"), str):
            continue
        try:
            patch_path = Path(worker["patch_file"]).resolve()
            ensure_inside(repo_root, patch_path)
            patch_paths.append(patch_path.relative_to(repo_root).as_posix())
        except (OSError, ValueError):
            continue

    result = run_native_agent(
        config=config,
        repo_root=repo_root,
        worktree=repo_root,
        prompt=f"""Review Farm {farm_id} as the final high-capability Supervisor.

Read the evidence package at `{review_relative}` and inspect the candidate patch files:
{chr(10).join(f'- `{path}`' for path in patch_paths) or '- No readable patch files were produced.'}

Select at most one Worker. Approve only a machine-review-passing patch that materially satisfies
the original Worker Plan, has adequate verification, and introduces no unacceptable correctness,
security, or maintainability risk. Otherwise request revision, reject, or hold for user input.
The task_id must be exactly `{farm_id}`. approved_worker must be null unless decision is
approve_merge. Call submit_supervisor_decision with your final structured decision.
""",
        system_prompt=(
            "You are Agent Farm's final high-capability software reviewer. Use read-only tools to "
            "inspect actual evidence and diffs. Be skeptical, concise, and never modify files."
        ),
        provider=config.supervisor_provider or config.worker_provider,
        model=config.supervisor_model or config.worker_model,
        timeout_seconds=None,
        writable=False,
        events_file=farm_dir / "supervisor-review-events.jsonl",
        terminal_tool={
            "name": "submit_supervisor_decision",
            "description": "Submit the final Supervisor review decision.",
            "parameters": SUPERVISOR_DECISION_SCHEMA,
        },
        reasoning_mode=config.supervisor_reasoning_mode,
        reasoning_effort=config.supervisor_reasoning_effort,
        event_callback=event_callback,
        approval_callback=approval_callback,
        cancel_check=cancel_check,
        usage_context={
            "farm_id": farm_id,
            "agent_id": "supervisor-review",
            "agent_kind": "supervisor",
            "phase": "review",
        },
    )
    if not result.ok:
        raise SupervisorError(result.error or "Native Supervisor review failed.")
    raw = result.terminal_payload
    if raw is None:
        try:
            raw = json.loads(result.final_text)
        except json.JSONDecodeError as exc:
            raise SupervisorError("Native Supervisor did not submit a decision.") from exc
    try:
        decision = SupervisorDecision.from_dict(raw)
    except ValueError as exc:
        raise SupervisorError(f"Native Supervisor returned an invalid decision: {exc}") from exc
    if decision.task_id != farm_id:
        raise SupervisorError("Native Supervisor changed the protected Farm task_id.")
    return decision
