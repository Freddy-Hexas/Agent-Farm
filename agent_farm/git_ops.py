from __future__ import annotations

import fnmatch
import os
import tempfile
from pathlib import Path

from .models import ChangedFile, CommandResult
from .util import run_command


class GitError(RuntimeError):
    pass


def git(
    repo: Path,
    args: list[str],
    *,
    timeout_seconds: int | None = None,
    env: dict[str, str] | None = None,
) -> CommandResult:
    result = run_command(
        ["git", *args],
        repo,
        timeout_seconds=timeout_seconds,
        env=env,
    )
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


def create_workspace_snapshot(
    repo: Path,
    base_commit: str,
    *,
    include_paths: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
) -> str:
    """Return a detached commit representing the current usable workspace state.

    Worker worktrees must see the same files that the Supervisor planned against, including
    uncommitted implementation files. An alternate index creates the snapshot without touching
    the user's staging area, branch, or working files.
    """

    repo_root = find_repo_root(repo)
    tracked = _split_nul_paths(git(repo_root, ["ls-files", "-z"]).stdout)
    untracked = _split_nul_paths(
        git(repo_root, ["ls-files", "--others", "--exclude-standard", "-z"]).stdout
    )
    candidates = sorted(set(tracked + untracked))
    allowed = [
        path
        for path in candidates
        if (not include_paths or _matches_any(path, include_paths))
        and not _matches_any(path, forbidden_paths or [])
    ]
    if not allowed:
        return base_commit

    file_descriptor, raw_index_path = tempfile.mkstemp(
        prefix="agent-farm-snapshot-",
        dir=repo_root / ".git",
    )
    os.close(file_descriptor)
    index_path = Path(raw_index_path)
    index_path.unlink(missing_ok=True)
    environment = dict(os.environ)
    environment.update(
        {
            "GIT_INDEX_FILE": str(index_path),
            "GIT_AUTHOR_NAME": "Agent Farm Snapshot",
            "GIT_AUTHOR_EMAIL": "snapshot@agentfarm.local",
            "GIT_COMMITTER_NAME": "Agent Farm Snapshot",
            "GIT_COMMITTER_EMAIL": "snapshot@agentfarm.local",
        }
    )
    try:
        git(repo_root, ["read-tree", base_commit], env=environment)
        for start in range(0, len(allowed), 32):
            git(repo_root, ["add", "-A", "--", *allowed[start : start + 32]], env=environment)
        tree = git(repo_root, ["write-tree"], env=environment).stdout.strip()
        base_tree = git(repo_root, ["rev-parse", f"{base_commit}^{{tree}}"], env=environment).stdout.strip()
        if tree == base_tree:
            return base_commit
        return git(
            repo_root,
            [
                "commit-tree",
                tree,
                "-p",
                base_commit,
                "-m",
                "Agent Farm workspace snapshot",
            ],
            env=environment,
        ).stdout.strip()
    finally:
        index_path.unlink(missing_ok=True)
        Path(str(index_path) + ".lock").unlink(missing_ok=True)


def remove_worktree(repo: Path, worktree: Path, *, force: bool = False) -> None:
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(worktree))
    git(repo, args)


def collect_patch(
    worktree: Path,
    *,
    include_ignored_paths: list[str] | None = None,
) -> str:
    mark_untracked_for_diff(worktree, include_ignored_paths=include_ignored_paths)
    return git(worktree, ["diff", "--binary", "--no-ext-diff"]).stdout


def collect_changed_files(
    worktree: Path,
    *,
    include_ignored_paths: list[str] | None = None,
) -> list[ChangedFile]:
    mark_untracked_for_diff(worktree)
    output = git(worktree, ["diff", "--name-status", "-z", "--no-ext-diff"]).stdout
    files: list[ChangedFile] = []
    if output:
        parts = output.split("\0")
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

    # Deliverables are often intentionally ignored (for example reports under
    # test-artifacts). Include only ignored files explicitly covered by the
    # Worker allowlist so machine review can validate artifact-only work without
    # exposing unrelated secrets or local state.
    if include_ignored_paths:
        ignored = git(
            worktree,
            ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"],
        ).stdout
        known = {item.review_path for item in files}
        for path in _split_nul_paths(ignored):
            if path in known or not _matches_any(path, include_ignored_paths):
                continue
            files.append(ChangedFile(status="A", path=path))
    return files


def _split_nul_paths(output: str) -> list[str]:
    return [part for part in output.split("\0") if part]


def _matches_any(path: str, patterns: list[str]) -> bool:
    normalized_path = path.replace("\\", "/").strip("/").casefold()
    for pattern in patterns:
        normalized_pattern = pattern.replace("\\", "/").strip("/").casefold()
        candidates = [normalized_pattern]
        if normalized_pattern.startswith("**/"):
            candidates.append(normalized_pattern[3:])
        if any(fnmatch.fnmatchcase(normalized_path, candidate) for candidate in candidates):
            return True
    return False


def mark_untracked_for_diff(
    worktree: Path,
    *,
    include_ignored_paths: list[str] | None = None,
) -> None:
    output = git(worktree, ["ls-files", "--others", "--exclude-standard", "-z"]).stdout
    paths = _split_nul_paths(output)
    if paths:
        git(worktree, ["add", "-N", "--", *paths])
    if include_ignored_paths:
        ignored = git(
            worktree,
            ["ls-files", "--others", "--ignored", "--exclude-standard", "-z"],
        ).stdout
        allowed_ignored = [
            path
            for path in _split_nul_paths(ignored)
            if _matches_any(path, include_ignored_paths)
        ]
        if allowed_ignored:
            # An explicitly allowlisted ignored artifact still needs a real
            # patch so the Supervisor/change-control path can apply it.
            git(worktree, ["add", "-N", "-f", "--", *allowed_ignored])


def apply_patch(repo: Path, patch_file: Path) -> None:
    git(repo, ["apply", "--check", str(patch_file)])
    git(repo, ["apply", str(patch_file)])


def is_worktree_dirty(repo: Path) -> bool:
    result = run_command(["git", "status", "--porcelain"], repo)
    if not result.ok:
        raise GitError(result.stderr.strip() or "Could not inspect git status.")
    return bool(result.stdout.strip())
