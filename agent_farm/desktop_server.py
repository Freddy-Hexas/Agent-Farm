from __future__ import annotations

import argparse
import json
import signal
import threading
from pathlib import Path

from .desktop import DesktopRuntime, discover_default_repo
from .git_ops import find_repo_root


READY_PREFIX = "AGENT_FARM_READY "


def native_runtime_url(runtime: DesktopRuntime) -> str:
    """Return the UI URL without enabling the legacy pywebview chrome."""

    return runtime.url.replace("?desktop=1", "?native=1")


def ready_message(url: str) -> str:
    return READY_PREFIX + json.dumps({"url": url}, separators=(",", ":"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-farm-runtime")
    parser.add_argument(
        "--repo",
        type=Path,
        default=discover_default_repo(),
        help="Repository served by the Agent Farm runtime.",
    )
    parser.add_argument("--config", type=Path, default=None, help="Config JSON path.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    stop_event = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    for signal_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        current_signal = getattr(signal, signal_name, None)
        if current_signal is not None:
            signal.signal(current_signal, request_stop)

    runtime = DesktopRuntime.start(
        repo_root=find_repo_root(args.repo),
        config_path=args.config,
    )
    try:
        print(ready_message(native_runtime_url(runtime)), flush=True)
        stop_event.wait()
    finally:
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
