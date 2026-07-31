from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import CONFIG_FILE, write_default_config, write_local_templates
from .farm import record_supervisor_decision, review_farm, run_farm
from .orchestrator import cleanup_run, merge_run, prepare_dry_run, review_run, run_task


def _add_common_run_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository path.")
    parser.add_argument("--config", type=Path, default=None, help="Config JSON path.")
    parser.add_argument("--base", default="HEAD", help="Base git ref for the worker worktree.")
    parser.add_argument("--model", default=None, help="Worker model passed to Codex.")
    parser.add_argument(
        "--profile",
        default=None,
        help="Named cheap/mid worker profile from worker_profiles.",
    )
    parser.add_argument("--provider", default=None, help="Worker model provider id from config.")
    parser.add_argument("--secrets-env", default=None, help="Gitignored env file containing provider API keys.")
    parser.add_argument("--oss", action="store_true", help="Run Codex with --oss.")
    parser.add_argument("--local-provider", choices=["ollama", "lmstudio"], default=None, help="Local OSS provider.")
    parser.add_argument("--codex-profile", default=None, help="Codex user-level profile to select.")
    parser.add_argument("--codex-profile-v2", default=None, help="Codex profile-v2 config layer to select.")
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

    local_parser = subparsers.add_parser("init-local", help="Write gitignored local provider templates.")
    local_parser.add_argument("--config-path", type=Path, default=Path("agent-farm.local.json"), help="Local config path.")
    local_parser.add_argument("--secrets-path", type=Path, default=Path(".agent-farm/secrets.env"), help="Secrets env path.")
    local_parser.add_argument("--force", action="store_true", help="Overwrite existing local files.")

    run_parser = subparsers.add_parser("run", help="Run one autonomous Worker in an isolated worktree.")
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

    farm_run_parser = subparsers.add_parser(
        "farm-run",
        help="Run a supervisor-authored worker plan with isolated model profiles.",
    )
    farm_run_parser.add_argument("--plan", type=Path, required=True, help="Worker Plan JSON file.")
    farm_run_parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository path.")
    farm_run_parser.add_argument("--config", type=Path, default=None, help="Config JSON path.")

    farm_review_parser = subparsers.add_parser(
        "farm-review",
        help="Print the aggregate review package for a farm run.",
    )
    farm_review_parser.add_argument("--farm", type=Path, required=True, help="Farm run directory.")

    farm_decide_parser = subparsers.add_parser(
        "farm-decide",
        help="Record the expensive supervisor's structured decision.",
    )
    farm_decide_parser.add_argument("--farm", type=Path, required=True, help="Farm run directory.")
    farm_decide_parser.add_argument(
        "--decision",
        type=Path,
        required=True,
        help="Supervisor Decision JSON file.",
    )

    ui_parser = subparsers.add_parser(
        "ui",
        help="Open the local Agent Farm Web console.",
    )
    ui_parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository path.")
    ui_parser.add_argument("--config", type=Path, default=None, help="Config JSON path.")
    ui_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Loopback address to bind (default: 127.0.0.1).",
    )
    ui_parser.add_argument("--port", type=int, default=8765, help="Local port (default: 8765).")
    ui_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the default browser automatically.",
    )

    desktop_parser = subparsers.add_parser(
        "desktop",
        help="Launch Agent Farm in a native desktop window.",
    )
    desktop_parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository path.")
    desktop_parser.add_argument("--config", type=Path, default=None, help="Config JSON path.")
    desktop_parser.add_argument("--width", type=int, default=1440, help="Initial window width.")
    desktop_parser.add_argument("--height", type=int, default=900, help="Initial window height.")
    desktop_parser.add_argument("--debug", action="store_true", help="Enable WebView developer tools.")

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


def print_farm_summary(result: dict) -> None:
    print(f"farm_id: {result.get('farm_id')}")
    print(f"status: {result.get('status')}")
    print(f"farm_dir: {result.get('farm_dir')}")
    print(f"review_package: {result.get('review_package')}")
    workers = result.get("workers") or []
    print(f"workers: {len(workers)}")
    for worker in workers:
        review = worker.get("machine_review") or {}
        print(
            f"  {worker.get('id')} role={worker.get('role')} profile={worker.get('profile')} "
            f"model={worker.get('model')} provider={worker.get('provider')} "
            f"status={worker.get('status')} machine_review={review.get('status')}"
        )
        if worker.get("error"):
            print(f"    error: {worker['error'].get('message')}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            write_default_config(args.path, force=args.force)
            print(f"created {args.path}")
            return 0

        if args.command == "init-local":
            write_local_templates(
                config_path=args.config_path,
                secrets_path=args.secrets_path,
                force=args.force,
            )
            print(f"created {args.config_path}")
            print(f"created {args.secrets_path}")
            return 0

        if args.command == "run":
            worker_oss = True if args.oss else None
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
                    provider=args.provider,
                    secrets_env=args.secrets_env,
                    worker_oss=worker_oss,
                    local_provider=args.local_provider,
                    codex_profile=args.codex_profile,
                    codex_profile_v2=args.codex_profile_v2,
                    profile=args.profile,
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
                provider=args.provider,
                secrets_env=args.secrets_env,
                worker_oss=worker_oss,
                local_provider=args.local_provider,
                codex_profile=args.codex_profile,
                codex_profile_v2=args.codex_profile_v2,
                profile=args.profile,
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

        if args.command == "farm-run":
            result = run_farm(repo=args.repo, plan_file=args.plan, config_path=args.config)
            print_farm_summary(result)
            return 0

        if args.command == "farm-review":
            print_farm_summary(review_farm(args.farm))
            return 0

        if args.command == "farm-decide":
            result = record_supervisor_decision(args.farm, args.decision)
            print_farm_summary(result)
            return 0

        if args.command == "ui":
            from .web_server import serve_console

            serve_console(
                repo=args.repo,
                config_path=args.config,
                host=args.host,
                port=args.port,
                open_browser=not args.no_browser,
            )
            return 0

        if args.command == "desktop":
            from .desktop import run_desktop

            run_desktop(
                repo=args.repo,
                config_path=args.config,
                width=args.width,
                height=args.height,
                debug=args.debug,
            )
            return 0

    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
