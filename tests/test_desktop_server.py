from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from agent_farm.daemon_runtime import write_descriptor
from agent_farm.desktop_server import (
    READY_PREFIX,
    native_runtime_url,
    probe_descriptor,
    ready_message,
    runtime_fingerprint_matches,
)


ROOT = Path(__file__).resolve().parents[1]


def test_native_runtime_url_disables_legacy_desktop_chrome() -> None:
    runtime = SimpleNamespace(url="http://127.0.0.1:43123/?desktop=1")

    assert native_runtime_url(runtime) == "http://127.0.0.1:43123/?native=1"


def test_ready_message_is_machine_readable() -> None:
    message = ready_message("http://127.0.0.1:43123/?native=1")

    assert message.startswith(READY_PREFIX)
    assert json.loads(message[len(READY_PREFIX) :]) == {
        "url": "http://127.0.0.1:43123/?native=1"
    }


def test_runtime_reuse_requires_matching_descriptor_and_health_fingerprints() -> None:
    descriptor = {
        "runtime_fingerprint": "current-build",
        "health": {"runtime_fingerprint": "current-build"},
    }

    assert runtime_fingerprint_matches(descriptor, expected_fingerprint="current-build")
    assert not runtime_fingerprint_matches(descriptor, expected_fingerprint="new-build")
    assert runtime_fingerprint_matches(descriptor, expected_fingerprint="")


def test_daemon_is_single_instance_reports_health_and_stops_gracefully() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        env["AGENT_FARM_RUNTIME_FINGERPRINT"] = "desktop-server-integration"
        command = [
            sys.executable,
            "-m",
            "agent_farm.desktop_server",
            "--repo",
            str(repo),
        ]
        descriptor_path = repo / ".agent-farm" / "runtime.json"
        write_descriptor(
            descriptor_path,
            {
                "pid": 999999,
                "url": "http://127.0.0.1:9/?native=1",
                "repository": str(repo.resolve()),
            },
        )
        daemon = subprocess.Popen(
            [*command, "--daemon"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            descriptor = None
            deadline = time.monotonic() + 10
            while descriptor is None and time.monotonic() < deadline:
                time.sleep(0.05)
                descriptor = probe_descriptor(descriptor_path)
            assert descriptor is not None
            assert descriptor["pid"] == daemon.pid
            assert descriptor["health"]["status"] == "ok"
            assert descriptor["health"]["runtime_fingerprint"] == "desktop-server-integration"

            duplicate = subprocess.run(
                [*command, "--daemon"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert duplicate.returncode == 0
            assert duplicate.stdout.startswith(READY_PREFIX)

            status = subprocess.run(
                [*command, "--status"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert status.returncode == 0
            assert json.loads(status.stdout)["running"] is True

            stopped = subprocess.run(
                [*command, "--stop"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
            )
            assert stopped.returncode == 0
            assert json.loads(stopped.stdout)["stop_requested"] is True
            assert daemon.wait(timeout=10) == 0
            assert probe_descriptor(descriptor_path) is None
        finally:
            if daemon.poll() is None:
                daemon.kill()
                daemon.wait(timeout=5)
