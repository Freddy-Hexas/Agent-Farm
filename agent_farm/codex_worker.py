from __future__ import annotations

from pathlib import Path

from .models import AgentFarmConfig, CommandResult, RunPaths
from .util import run_command


def build_codex_args(
    *,
    config: AgentFarmConfig,
    worktree: Path,
    final_message_file: Path,
    model: str | None,
) -> list[str]:
    args = [
        config.codex_binary,
        "exec",
        "--cd",
        str(worktree),
        "--sandbox",
        config.sandbox,
        "--ask-for-approval",
        config.approval_policy,
        "--output-last-message",
        str(final_message_file),
    ]
    if config.ephemeral:
        args.append("--ephemeral")
    if config.codex_json:
        args.append("--json")
    selected_model = model or config.worker_model
    if selected_model:
        args.extend(["--model", selected_model])
    args.append("-")
    return args


def run_codex_worker(
    *,
    config: AgentFarmConfig,
    paths: RunPaths,
    prompt: str,
    model: str | None,
    timeout_seconds: int | None,
) -> CommandResult:
    args = build_codex_args(
        config=config,
        worktree=paths.worktree,
        final_message_file=paths.worker_final_file,
        model=model,
    )
    result = run_command(
        args,
        paths.repo_root,
        timeout_seconds=timeout_seconds or config.timeout_seconds,
        input_text=prompt,
    )
    paths.worker_events_file.write_text(result.stdout, encoding="utf-8")
    paths.worker_stderr_file.write_text(result.stderr, encoding="utf-8")
    return result
