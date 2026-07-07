from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import CONFIG_FILE, write_default_config
from .orchestrator import cleanup_run, merge_run, prepare_dry_run, review_run, run_task


def _add_common_run_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository path.")
    parser.add_argument("--config", type=Path, default=None, help="Config JSON path.")
    parser.add_argument("--base", default="HEAD", help="Base git ref for the worker worktree.")
    parser.add_argument("--model", default=None, help="Worker model passed to Codex.")
    parser.add_argument("--allow", action="append", default=None, help="Allowed path or glob. Repeatable.")
    parser.add_argument("--forbid", action="append", default=None, help="Forbidden path or glob. Repeatable.")
    parser.add_argument("--test-cmd", action="append", default=None, help="Check command rerun by orchestrator. Repeatable.")
    parser.add_argument("--timeout", type=int, default=None, help="Worker timeout in seconds.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-farm")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Write a default config file.")
    init_parser.add_argument("--path", type=Path, default=Path(CONFIG_FILE), help="Config path to create.")
    init_parser.add_argument("--force", action="store_true", help="Overwrite an existing config.")

    run_parser = subparsers.add_parser("run", help="Run one Codex worker in an isolated worktree.")
    run_parser.add_argument("--task", type=Path, required=True, help="Markdown task spec for the worker.")
    run_parser.add_argument("--dry-run", action="store_true", help="Print the worker prompt without running Codex.")
    _add_common_run_options(run_parser)

    review_parser = subparsers.add_parser("review", help="Print a run summary.")
    review_parser.add_argument("--run", type=Path, required=True, help="Run directory.")

    merge_parser = subparsers.add_parser("merge", help="Apply a reviewed worker patch to the supervisor workspace.")
    merge_parser.add_argument("--run", type=Path, required=True, help="Run directory.")
    merge_parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository path.")
    merge_parser.add_argument("--yes", action="store_true", help="Confirm patch application.")
    merge_parser.add_argument("--allow-dirty", action="store_true", help="Allow merging into a dirty workspace.")

    cleanup_parser = subparsers.add_parser("cleanup", help="Remove a worker worktree.")
    cleanup_parser.add_argument("--run", type=Path, required=True, help="Run directory.")
    cleanup_parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository path.")
    cleanup_parser.add_argument("--force", action="store_true", help="Force worktree removal.")

    return parser


def print_run_summary(result: dict) -> None:
    print(f"task_id: {result.get('task_id')}")
    print(f"status: {result.get('status')}")
    print(f"run_dir: {result.get('run_dir')}")
    print(f"worktree: {result.get('worktree')}")
    print(f"patch_file: {result.get('patch_file')}")

    changed = result.get("changed_files") or []
    print(f"changed_files: {len(changed)}")
    for item in changed:
        status = item.get("status")
        path = item.get("path")
        old_path = item.get("old_path")
        if old_path:
            print(f"  {status} {old_path} -> {path}")
        else:
            print(f"  {status} {path}")

    tests = result.get("tests") or []
    print(f"tests: {len(tests)}")
    for test in tests:
        marker = "ok" if test.get("returncode") == 0 and not test.get("timed_out") else "failed"
        print(f"  {marker} {test.get('command')} ({test.get('log_file')})")

    review = result.get("machine_review") or {}
    print(f"machine_review: {review.get('status')}")
    for finding in review.get("findings") or []:
        path = f" [{finding.get('path')}]" if finding.get("path") else ""
        print(f"  {finding.get('severity')} {finding.get('code')}{path}: {finding.get('message')}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            write_default_config(args.path, force=args.force)
            print(f"created {args.path}")
            return 0

        if args.command == "run":
            if args.dry_run:
                prompt = prepare_dry_run(
                    repo=args.repo,
                    task_file=args.task,
                    config_path=args.config,
                    base_ref=args.base,
                    allowed_paths=args.allow,
                    forbidden_paths=args.forbid,
                    test_commands=args.test_cmd,
                    timeout_seconds=args.timeout,
                    model=args.model,
                )
                print(prompt)
                return 0

            result = run_task(
                repo=args.repo,
                task_file=args.task,
                config_path=args.config,
                base_ref=args.base,
                allowed_paths=args.allow,
                forbidden_paths=args.forbid,
                test_commands=args.test_cmd,
                timeout_seconds=args.timeout,
                model=args.model,
            )
            print_run_summary(result)
            return 0

        if args.command == "review":
            print_run_summary(review_run(args.run))
            return 0

        if args.command == "merge":
            result = merge_run(args.run, repo=args.repo, yes=args.yes, allow_dirty=args.allow_dirty)
            print_run_summary(result)
            return 0

        if args.command == "cleanup":
            cleanup_run(args.run, repo=args.repo, force=args.force)
            print("worktree removed")
            return 0

    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
