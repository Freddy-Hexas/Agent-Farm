from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath

from .models import AgentFarmConfig, ChangedFile, MachineReview, ReviewFinding, TestResult

LOCKFILE_NAMES = {
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lockb",
    "poetry.lock",
    "Pipfile.lock",
    "uv.lock",
    "Cargo.lock",
    "Gemfile.lock",
    "composer.lock",
}


def normalize_path(path: str) -> str:
    return path.replace("\\", "/").strip("/")


def path_matches(pattern: str, path: str) -> bool:
    normalized_pattern = normalize_path(pattern).casefold()
    normalized_path = normalize_path(path).casefold()
    if not normalized_pattern:
        return False

    has_glob = any(char in normalized_pattern for char in "*?[]")
    if has_glob:
        patterns = [normalized_pattern]
        if normalized_pattern.startswith("**/"):
            patterns.append(normalized_pattern[3:])
        return any(fnmatch.fnmatchcase(normalized_path, candidate) for candidate in patterns)

    return normalized_path == normalized_pattern or normalized_path.startswith(
        normalized_pattern.rstrip("/") + "/"
    )


def is_test_path(path: str) -> bool:
    normalized = normalize_path(path).lower()
    name = PurePosixPath(normalized).name
    return (
        "/test/" in f"/{normalized}/"
        or "/tests/" in f"/{normalized}/"
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith(".test.ts")
        or name.endswith(".test.tsx")
        or name.endswith(".spec.ts")
        or name.endswith(".spec.tsx")
    )


def is_lockfile(path: str) -> bool:
    return PurePosixPath(normalize_path(path)).name in LOCKFILE_NAMES


def count_diff_lines(patch: str) -> int:
    return sum(1 for line in patch.splitlines() if line.startswith(("+", "-")) and not line.startswith(("+++", "---")))


def run_machine_review(
    config: AgentFarmConfig,
    changed_files: list[ChangedFile],
    patch: str,
    test_results: list[TestResult],
    *,
    worker_ok: bool = True,
    worker_failure_message: str | None = None,
    allow_no_changes: bool = False,
) -> MachineReview:
    findings: list[ReviewFinding] = []

    if not worker_ok:
        findings.append(
            ReviewFinding(
                severity="error",
                code="worker_failed",
                message=worker_failure_message or "Worker agent exited unsuccessfully.",
            )
        )

    if not changed_files and not allow_no_changes:
        findings.append(
            ReviewFinding(
                severity="error",
                code="no_changes",
                message="Worker produced no git diff.",
            )
        )
    elif not changed_files:
        findings.append(
            ReviewFinding(
                severity="warning",
                code="no_changes_allowed",
                message="Worker completed without a git diff; this Worker was explicitly marked analysis-only.",
            )
        )

    if len(changed_files) > config.max_changed_files:
        findings.append(
            ReviewFinding(
                severity="error",
                code="too_many_files",
                message=f"Changed {len(changed_files)} files; limit is {config.max_changed_files}.",
            )
        )

    diff_lines = count_diff_lines(patch)
    if diff_lines > config.max_diff_lines:
        findings.append(
            ReviewFinding(
                severity="error",
                code="diff_too_large",
                message=f"Diff has {diff_lines} changed lines; limit is {config.max_diff_lines}.",
            )
        )

    for changed in changed_files:
        review_paths = [changed.review_path]
        if changed.old_path and changed.old_path not in review_paths:
            review_paths.append(changed.old_path)
        for path in review_paths:
            if config.allowed_paths and not any(
                path_matches(pattern, path) for pattern in config.allowed_paths
            ):
                findings.append(
                    ReviewFinding(
                        severity="error",
                        code="outside_allowed_paths",
                        path=path,
                        message="Changed file is outside the allowed path set.",
                    )
                )

            matched_forbidden = next(
                (pattern for pattern in config.forbidden_paths if path_matches(pattern, path)),
                None,
            )
            if matched_forbidden:
                findings.append(
                    ReviewFinding(
                        severity="error",
                        code="forbidden_path",
                        path=path,
                        message=f"Changed file matches forbidden pattern: {matched_forbidden}",
                    )
                )

        path = changed.review_path
        if is_lockfile(path) and not config.allow_lockfiles:
            findings.append(
                ReviewFinding(
                    severity="error",
                    code="lockfile_changed",
                    path=path,
                    message="Lockfile changed but allow_lockfiles is false.",
                )
            )

        if changed.status.startswith("D") and is_test_path(path):
            findings.append(
                ReviewFinding(
                    severity="error",
                    code="deleted_test",
                    path=path,
                    message="Worker deleted a test file.",
                )
            )

    if not test_results:
        findings.append(
            ReviewFinding(
                severity="warning",
                code="no_orchestrator_tests",
                message="No orchestrator test commands were configured.",
            )
        )
    else:
        for result in test_results:
            if not result.ok:
                findings.append(
                    ReviewFinding(
                        severity="error",
                        code="test_failed",
                        message=f"Check failed: {result.command}",
                    )
                )

    status = "failed" if any(f.severity == "error" for f in findings) else "passed"
    return MachineReview(status=status, findings=findings)
