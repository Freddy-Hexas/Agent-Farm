from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from .models import AgentFarmConfig


def read_task_spec(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Task spec not found: {path}")
    return path.read_text(encoding="utf-8-sig").strip()


def slugify(value: str, *, fallback: str = "task") -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-._").lower()
    return slug or fallback


def make_task_id(task_path: Path) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{slugify(task_path.stem)}"


def format_list(items: list[str], *, empty: str) -> str:
    if not items:
        return empty
    return "\n".join(f"- {item}" for item in items)


def build_worker_prompt(
    *,
    task_spec: str,
    base_commit: str,
    config: AgentFarmConfig,
) -> str:
    allowed = format_list(config.allowed_paths, empty="- No explicit allowlist was provided.")
    forbidden = format_list(config.forbidden_paths, empty="- No explicit forbidden paths were provided.")
    tests = format_list(config.test_commands, empty="- No orchestrator test commands were provided.")

    return f"""# Codex Worker Task

You are a worker agent running under Agent Farm. Complete only the task below.

## Base Commit

`{base_commit}`

## Task Spec

{task_spec}

## Allowed Paths

{allowed}

If an allowlist is present, keep all edits inside it.

## Forbidden Paths

{forbidden}

Do not edit forbidden paths. Do not edit secrets, environment files, git metadata,
CI/deploy files, or lockfiles unless the task spec explicitly requires it.

## Worker Rules

1. Work only in this isolated git worktree.
2. Do not merge, push, commit, or change remotes.
3. Keep the patch small and directly tied to the task.
4. Prefer existing project patterns over new abstractions.
5. Add or update focused tests when the task changes behavior.
6. Run the relevant checks you can run locally.
7. If the task cannot be completed safely, leave the tree unchanged and explain why.

## Orchestrator Checks

The orchestrator will rerun these commands after you finish:

{tests}

## Final Response Required

Return a concise report with:

1. Modified files.
2. Checks you ran and their results.
3. Any unresolved risks or follow-up needed.
4. Confirmation that you did not merge, push, or commit.
"""
