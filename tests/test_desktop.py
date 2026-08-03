import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import urlopen

from agent_farm.desktop import (
    DesktopRuntime,
    DesktopWindowBridge,
    _enforce_initial_window_state,
    _maximize_to_work_area,
    discover_default_repo,
)
from unittest.mock import patch


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


class DesktopRuntimeTests(unittest.TestCase):
    def test_startup_reapplies_maximize_after_native_gui_starts(self):
        class FakeWindow:
            def __init__(self):
                self.maximize_calls = 0

            def maximize(self):
                self.maximize_calls += 1

        window = FakeWindow()
        _enforce_initial_window_state(window, maximized=True)
        self.assertEqual(window.maximize_calls, 1)

        _enforce_initial_window_state(window, maximized=False)
        self.assertEqual(window.maximize_calls, 1)

    def test_work_area_maximize_falls_back_only_when_native_path_is_unavailable(self):
        class FakeWindow:
            def __init__(self):
                self.maximize_calls = 0

            def maximize(self):
                self.maximize_calls += 1

        window = FakeWindow()
        with patch("agent_farm.desktop._maximize_windows_work_area", return_value=True):
            _maximize_to_work_area(window)
        self.assertEqual(window.maximize_calls, 0)

        with patch("agent_farm.desktop._maximize_windows_work_area", return_value=False):
            _maximize_to_work_area(window)
        self.assertEqual(window.maximize_calls, 1)

    def test_frozen_executable_finds_repository_above_dist_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init")
            dist = root / "dist"
            dist.mkdir()
            unrelated = root / "outside"
            unrelated.mkdir()

            discovered = discover_default_repo(
                cwd=unrelated,
                executable=dist / "AgentFarm.exe",
                frozen=True,
            )

            self.assertEqual(discovered, root.resolve())

    def test_native_runtime_owns_local_server_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init")
            (root / "README.md").write_text("desktop\n", encoding="utf-8")
            git(root, "add", "README.md")
            git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-m", "init")
            (root / "agent-farm.config.json").write_text(
                json.dumps(
                    {
                        "worker_profiles": {"cheap": {"model": "budget"}},
                        "default_worker_profile": "cheap",
                    }
                ),
                encoding="utf-8",
            )
            runtime = DesktopRuntime.start(repo_root=root, config_path=None)
            try:
                self.assertIn("?desktop=1", runtime.url)
                with urlopen(runtime.url, timeout=2) as response:
                    html = response.read().decode("utf-8")
                self.assertIn("What should we build", html)
                self.assertIn('class="sidebar"', html)
                self.assertIn('class="composer-dock"', html)
                self.assertIn('id="inspector"', html)
                self.assertTrue(runtime.server_thread.is_alive())
            finally:
                runtime.close()
            self.assertFalse(runtime.server_thread.is_alive())
            runtime.close()  # Closing twice is intentionally safe.

    def test_native_runtime_can_run_as_api_only_without_web_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init")
            (root / "README.md").write_text("desktop\n", encoding="utf-8")
            git(root, "add", "README.md")
            git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-m", "init")
            runtime = DesktopRuntime.start(
                repo_root=root,
                config_path=None,
                serve_assets=False,
            )
            try:
                with self.assertRaises(HTTPError) as missing:
                    urlopen(runtime.url, timeout=2)
                self.assertEqual(getattr(missing.exception, "code", None), 404)
                api_url = runtime.url.split("?", 1)[0].rstrip("/") + "/api/bootstrap"
                with urlopen(api_url, timeout=2) as response:
                    payload = json.loads(response.read())
                self.assertEqual(payload["repository"]["name"], root.name)
            finally:
                runtime.close()

    def test_frameless_window_bridge_allows_only_window_chrome_actions(self):
        class FakeEvent:
            def __init__(self):
                self.callback = None

            def __iadd__(self, callback):
                self.callback = callback
                return self

        class FakeWindow:
            def __init__(self):
                self.events = SimpleNamespace(maximized=FakeEvent(), restored=FakeEvent())
                self.calls = []

            def minimize(self):
                self.calls.append("minimize")

            def maximize(self):
                self.calls.append("maximize")

            def restore(self):
                self.calls.append("restore")

            def destroy(self):
                self.calls.append("destroy")

            def run_js(self, script):
                self.calls.append(script)

        window = FakeWindow()
        bridge = DesktopWindowBridge()
        bridge.bind(window)
        self.assertEqual(bridge.window_state(), {"maximized": False})
        self.assertEqual(bridge.window_action("minimize"), {"maximized": False})
        self.assertEqual(bridge.window_action("toggle_maximize"), {"maximized": True})
        self.assertEqual(bridge.window_action("toggle_maximize"), {"maximized": False})
        bridge.window_action("close")
        self.assertEqual(window.calls[:4], ["minimize", "maximize", "restore", "destroy"])
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            bridge.window_action("open_terminal")

        maximized_window = FakeWindow()
        maximized_bridge = DesktopWindowBridge(initially_maximized=True)
        maximized_bridge.bind(maximized_window)
        self.assertEqual(maximized_bridge.window_state(), {"maximized": True})
        self.assertEqual(
            maximized_bridge.window_action("toggle_maximize"),
            {"maximized": False},
        )
        self.assertEqual(maximized_window.calls[0], "restore")


if __name__ == "__main__":
    unittest.main()
