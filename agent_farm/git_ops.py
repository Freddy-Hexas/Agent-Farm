from __future__ import annotations

from pathlib import Path

from .models import ChangedFile, CommandResult
from .util import run_command


class GitError(RuntimeError):
    pass


def git(repo: Path, args: list[str], *, timeout_seconds: int | None = None) -> CommandResult:
    result = run_command(["git", *args], repo, timeout_seconds=timeout_seconds)
    if not result.ok:
        raise GitError(result.stderr.strip() or result.stdout.strip() or "git command failed")
    return result


def find_repo_root(start: Path) -> Path:
    result = run_command(["git", "rev-parse", "--show-toplevel"], start)
    if not result.ok:
        raise GitError("Agent Farm must run inside a git repository.")
    return Path(result.stdout.strip()).resolve()


def resolve_ref(repo: Path, ref: str) -> str:
    result = git(repo, ["rev-parse", "--verify", ref])
    commit = result.stdout.strip()
    if not commit:
        raise GitError(f"Could not resolve git ref: {ref}")
    return commit


def create_worktree(repo: Path, worktree: Path, base_ref: str) -> None:
    worktree.parent.mkdir(parents=True, exist_ok=True)
    git(repo, ["worktree", "add", "--detach", str(worktree), base_ref])


def remove_worktree(repo: Path, worktree: Path, *, force: bool = False) -> None:
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(worktree))
    git(repo, args)


def collect_patch(worktree: Path) -> str:
    mark_untracked_for_diff(worktree)
    return git(worktree, ["diff", "--binary", "--no-ext-diff"]).stdout


def collect_changed_files(worktree: Path) -> list[ChangedFile]:
    mark_untracked_for_diff(worktree)
    output = git(worktree, ["diff", "--name-status", "-z", "--no-ext-diff"]).stdout
    if not output:
        return []

    parts = output.split("\0")
    files: list[ChangedFile] = []
    index = 0
    while index < len(parts) and parts[index]:
        status = parts[index]
        index += 1
        if status.startswith(("R", "C")):
            old_path = parts[index]
            new_path = parts[index + 1]
            index += 2
            files.append(ChangedFile(status=status, old_path=old_path, path=new_path))
        else:
            path = parts[index]
            index += 1
            files.append(ChangedFile(status=status, path=path))
    return files


def _split_nul_paths(output: str) -> list[str]:
    return [part for part in output.split("\0") if part]


def mark_untracked_for_diff(worktree: Path) -> None:
    output = git(worktree, ["ls-files", "--others", "--exclude-standard", "-z"]).stdout
    paths = _split_nul_paths(output)
    if paths:
        git(worktree, ["add", "-N", "--", *paths])


def apply_patch(repo: Path, patch_file: Path) -> None:
    git(repo, ["apply", "--check", str(patch_file)])
    git(repo, ["apply", str(patch_file)])


def is_worktree_dirty(repo: Path) -> bool:
    result = run_command(["git", "status", "--porcelain"], repo)
    if not result.ok:
        raise GitError(result.stderr.strip() or "Could not inspect git status.")
    return bool(result.stdout.strip())
