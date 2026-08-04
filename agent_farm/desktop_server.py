from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from . import __version__
from .daemon_runtime import DaemonLease, read_descriptor, write_descriptor
from .desktop import DesktopRuntime, discover_default_repo
from .git_ops import find_repo_root


READY_PREFIX = "AGENT_FARM_READY "
DAEMON_START_TIMEOUT_SECONDS = 30


def native_runtime_url(runtime: DesktopRuntime) -> str:
    """Return the loopback authority consumed by the native JSON client."""

    return runtime.url.replace("?desktop=1", "?native=1")


def ready_message(url: str) -> str:
    return READY_PREFIX + json.dumps({"url": url}, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_paths(repo_root: Path) -> tuple[Path, Path, Path]:
    runtime_root = repo_root / ".agent-farm"
    return (
        runtime_root / "runtime.json",
        runtime_root / "runtime.lock",
        runtime_root / "logs" / "runtime.log",
    )


def _health_url(runtime_url: str) -> str:
    parsed = urlsplit(runtime_url)
    return f"{parsed.scheme}://{parsed.netloc}/api/health"


def _stop_url(runtime_url: str) -> str:
    parsed = urlsplit(runtime_url)
    return f"{parsed.scheme}://{parsed.netloc}/api/runtime/stop"


def probe_descriptor(path: Path, *, timeout: float = 1.0) -> dict[str, Any] | None:
    descriptor = read_descriptor(path)
    if descriptor is None or not isinstance(descriptor.get("url"), str):
        return None
    try:
        with urlopen(_health_url(descriptor["url"]), timeout=timeout) as response:
            health = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, HTTPError, URLError, json.JSONDecodeError):
        return None
    if not isinstance(health, dict) or health.get("status") != "ok":
        return None
    if health.get("protocol_version") != descriptor.get("protocol_version"):
        return None
    if health.get("repository") != descriptor.get("repository"):
        return None
    return {**descriptor, "health": health}


def runtime_fingerprint_matches(
    descriptor: dict[str, Any],
    *,
    expected_fingerprint: str | None = None,
) -> bool:
    expected = (
        expected_fingerprint
        if expected_fingerprint is not None
        else os.environ.get("AGENT_FARM_RUNTIME_FINGERPRINT", "")
    )
    if not expected:
        return True
    health = descriptor.get("health")
    return (
        descriptor.get("runtime_fingerprint") == expected
        and isinstance(health, dict)
        and health.get("runtime_fingerprint") == expected
    )


def request_daemon_stop(path: Path, *, timeout: float = 2.0) -> bool:
    descriptor = probe_descriptor(path, timeout=timeout)
    if descriptor is None:
        return False
    request = Request(
        _stop_url(descriptor["url"]),
        data=b"{}",
        headers={"Content-Type": "application/json", "Content-Length": "2"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status == 202
    except (OSError, HTTPError, URLError):
        return False


def _configure_daemon_logging(log_path: Path) -> object:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stream = log_path.open("a", encoding="utf-8", buffering=1)
    sys.stdout = stream
    sys.stderr = stream
    print(f"[{_utc_now()}] Agent Farm daemon starting (pid={os.getpid()}).", flush=True)
    return stream


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-farm-runtime")
    parser.add_argument(
        "--repo",
        type=Path,
        default=discover_default_repo(),
        help="Repository served by the Agent Farm runtime.",
    )
    parser.add_argument("--config", type=Path, default=None, help="Config JSON path.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--daemon", action="store_true", help="Run as the persistent repository daemon.")
    mode.add_argument("--status", action="store_true", help="Print daemon status as JSON.")
    mode.add_argument("--stop", action="store_true", help="Request a graceful daemon shutdown.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = find_repo_root(args.repo)
    descriptor_path, lease_path, log_path = _runtime_paths(repo_root)

    if args.status:
        descriptor = probe_descriptor(descriptor_path)
        print(json.dumps({"running": descriptor is not None, "runtime": descriptor}, ensure_ascii=True))
        return 0 if descriptor is not None else 1

    if args.stop:
        requested = request_daemon_stop(descriptor_path)
        if requested:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and probe_descriptor(descriptor_path) is not None:
                time.sleep(0.1)
        print(json.dumps({"stop_requested": requested}, ensure_ascii=True))
        return 0 if requested else 1

    stop_event = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    if threading.current_thread() is threading.main_thread():
        for signal_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            current_signal = getattr(signal, signal_name, None)
            if current_signal is not None:
                signal.signal(current_signal, request_stop)

    lease: DaemonLease | None = None
    log_stream: object | None = None
    if args.daemon:
        lease = DaemonLease(lease_path)
        if not lease.acquire():
            deadline = time.monotonic() + DAEMON_START_TIMEOUT_SECONDS
            acquired_after_handoff = False
            while time.monotonic() < deadline:
                descriptor = probe_descriptor(descriptor_path)
                if descriptor is not None and runtime_fingerprint_matches(descriptor):
                    print(ready_message(descriptor["url"]), flush=True)
                    return 0
                # A stopping daemon removes its descriptor just before it
                # releases the OS lease. Retry the lease so an upgrade can
                # take ownership across that narrow handoff window.
                if lease.acquire():
                    acquired_after_handoff = True
                    break
                time.sleep(0.1)
            if not acquired_after_handoff:
                print("error: another daemon owns the runtime lease but did not become healthy.", file=sys.stderr)
                return 1
        descriptor_path.unlink(missing_ok=True)
        log_stream = _configure_daemon_logging(log_path)

    runtime: DesktopRuntime | None = None
    try:
        runtime = DesktopRuntime.start(
            repo_root=repo_root,
            config_path=args.config,
            serve_assets=False,
            stop_callback=stop_event.set,
        )
        runtime_url = native_runtime_url(runtime)
        if args.daemon:
            write_descriptor(
                descriptor_path,
                {
                    "pid": os.getpid(),
                    "url": runtime_url,
                    "repository": str(repo_root),
                    "app_version": __version__,
                    "runtime_fingerprint": os.environ.get(
                        "AGENT_FARM_RUNTIME_FINGERPRINT", __version__
                    ),
                    "started_at": _utc_now(),
                },
            )
        print(ready_message(runtime_url), flush=True)
        stop_event.wait()
    finally:
        if runtime is not None:
            runtime.close()
        if args.daemon:
            current = read_descriptor(descriptor_path)
            if current is None or current.get("pid") == os.getpid():
                descriptor_path.unlink(missing_ok=True)
            print(f"[{_utc_now()}] Agent Farm daemon stopped.", flush=True)
        if lease is not None:
            lease.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
