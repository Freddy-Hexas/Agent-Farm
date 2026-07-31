from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .config import MODEL_PROVIDER_FIELDS
from .models import AgentFarmConfig, CommandResult, RunPaths
from .secrets import load_secrets_env
from .toml_util import toml_dotted_key, toml_literal
from .util import run_command

def _add_codex_config(args: list[str], key: str, value: Any) -> None:
    args.extend(["-c", f"{key}={toml_literal(value)}"])


def _add_provider_config(args: list[str], config: AgentFarmConfig) -> None:
    provider_id = config.worker_provider
    if not provider_id:
        return

    _add_codex_config(args, "model_provider", provider_id)
    provider = config.model_providers.get(provider_id)
    if not provider:
        return

    for field, value in provider.items():
        if value is None:
            continue
        if field not in MODEL_PROVIDER_FIELDS:
            raise ValueError(f"Unsupported model provider field for {provider_id}: {field}")
        if provider_id == "openai" and field == "base_url":
            _add_codex_config(args, "openai_base_url", value)
            continue
        if provider_id in {"openai", "ollama", "lmstudio"}:
            continue
        key = toml_dotted_key(["model_providers", provider_id, field])
        _add_codex_config(args, key, value)


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
        "--output-last-message",
        str(final_message_file),
    ]
    if config.worker_codex_profile:
        args.extend(["--profile", config.worker_codex_profile])
    if config.worker_codex_profile_v2:
        args.extend(["--profile-v2", config.worker_codex_profile_v2])
    if config.worker_oss:
        args.append("--oss")
    if config.worker_local_provider:
        args.extend(["--local-provider", config.worker_local_provider])
    if config.ephemeral:
        args.append("--ephemeral")
    if config.codex_json:
        args.append("--json")
    selected_model = model or config.worker_model
    if selected_model:
        args.extend(["--model", selected_model])
    _add_codex_config(args, "approval_policy", config.approval_policy)
    _add_provider_config(args, config)
    for key, value in config.codex_config_overrides.items():
        _add_codex_config(args, key, value)
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
    env = os.environ.copy()
    env.update(load_secrets_env(paths.repo_root, config.secrets_env))
    result = run_command(
        args,
        paths.repo_root,
        timeout_seconds=timeout_seconds or config.timeout_seconds,
        input_text=prompt,
        env=env,
    )
    paths.worker_events_file.write_text(result.stdout, encoding="utf-8")
    paths.worker_stderr_file.write_text(result.stderr, encoding="utf-8")
    return result
