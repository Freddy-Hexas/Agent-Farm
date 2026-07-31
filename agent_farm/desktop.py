from __future__ import annotations

import argparse
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .git_ops import find_repo_root
from .web_server import ConsoleHTTPServer, ConsoleState


class DesktopDependencyError(RuntimeError):
    pass


def _maximize_windows_work_area(window: Any) -> bool:
    """Maximize a frameless WinForms window inside its monitor work area."""

    if sys.platform != "win32":
        return False
    try:
        if not window.events.shown.wait(15):
            return False
        native = window.native
        if native is None:
            return False

        # pywebview uses a borderless WinForms Form for frameless windows. A
        # normal FormWindowState.Maximized can occupy the monitor bounds rather
        # than the taskbar-safe work area, and can inherit the wrong origin on
        # mixed-DPI/multi-monitor desktops. Pin MaximizedBounds to the monitor
        # that actually owns this form before changing its native state.
        from webview.platforms import winforms

        def apply_bounds() -> None:
            screen = winforms.WinForms.Screen.FromHandle(native.Handle)
            native.MaximizedBounds = screen.WorkingArea
            native.WindowState = winforms.WinForms.FormWindowState.Maximized

        if native.InvokeRequired:
            native.Invoke(winforms.WinForms.MethodInvoker(apply_bounds))
        else:
            apply_bounds()
        return True
    except Exception:
        return False


def _maximize_to_work_area(window: Any) -> None:
    if not _maximize_windows_work_area(window):
        window.maximize()


def _enforce_initial_window_state(window: Any, maximized: bool) -> None:
    """Apply startup state after the native window has entered its GUI loop."""

    if maximized:
        _maximize_to_work_area(window)


def discover_default_repo(
    *,
    cwd: Path | None = None,
    executable: Path | None = None,
    frozen: bool | None = None,
) -> Path:
    """Find a nearby Git repository for source and frozen desktop launches."""

    current = (cwd or Path.cwd()).resolve()
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    executable_path = (executable or Path(sys.executable)).resolve()
    candidates = [current]
    if is_frozen:
        candidates.extend([executable_path.parent, executable_path.parent.parent])
    else:
        candidates.append(Path(__file__).resolve().parents[1])
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if (candidate / ".git").exists():
            return candidate
    return current


def _report_startup_error(message: str) -> None:
    print(f"error: {message}")
    if not getattr(sys, "frozen", False):
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            f"Agent Farm could not start.\n\n{message}",
            "Agent Farm",
            0x10,
        )
    except Exception:
        pass


class DesktopWindowBridge:
    """Small, allowlisted bridge for frameless native window controls."""

    _ACTIONS = {"minimize", "toggle_maximize", "close"}
    _EDGES = {"n", "ne", "e", "se", "s", "sw", "w", "nw"}

    def __init__(
        self,
        *,
        min_width: int = 1040,
        min_height: int = 700,
        initially_maximized: bool = False,
    ) -> None:
        self._window: Any | None = None
        self._maximized = initially_maximized
        self._resize_state: tuple[str, float, float, int, int] | None = None
        self._lock = threading.RLock()
        self._min_width = min_width
        self._min_height = min_height

    def bind(self, window: Any) -> None:
        self._window = window
        window.events.maximized += self._on_maximized
        window.events.restored += self._on_restored

    def window_action(self, action: str) -> dict[str, bool]:
        if action not in self._ACTIONS:
            raise ValueError("Unsupported desktop window action.")
        window = self._require_window()
        if action == "minimize":
            window.minimize()
        elif action == "toggle_maximize":
            if self._maximized:
                window.restore()
                self._maximized = False
            else:
                _maximize_to_work_area(window)
                self._maximized = True
        else:
            window.destroy()
        return {"maximized": self._maximized}

    def window_state(self) -> dict[str, bool]:
        """Return renderer-safe window state without exposing the native object."""
        return {"maximized": self._maximized}

    def begin_resize(self, edge: str, screen_x: float, screen_y: float) -> dict[str, bool]:
        if edge not in self._EDGES:
            raise ValueError("Unsupported resize edge.")
        window = self._require_window()
        with self._lock:
            if self._maximized:
                return {"resizing": False}
            self._resize_state = (
                edge,
                float(screen_x),
                float(screen_y),
                int(window.width),
                int(window.height),
            )
        return {"resizing": True}

    def resize_window(self, screen_x: float, screen_y: float) -> dict[str, int | bool]:
        from webview.window import FixPoint

        window = self._require_window()
        with self._lock:
            if self._resize_state is None or self._maximized:
                return {"resizing": False}
            edge, start_x, start_y, start_width, start_height = self._resize_state
            dx = float(screen_x) - start_x
            dy = float(screen_y) - start_y
            width = start_width
            height = start_height
            if "e" in edge:
                width = max(self._min_width, round(start_width + dx))
            elif "w" in edge:
                width = max(self._min_width, round(start_width - dx))
            if "s" in edge:
                height = max(self._min_height, round(start_height + dy))
            elif "n" in edge:
                height = max(self._min_height, round(start_height - dy))

            if "w" in edge and "n" in edge:
                fix_point = FixPoint.EAST | FixPoint.SOUTH
            elif "w" in edge:
                fix_point = FixPoint.EAST | FixPoint.NORTH
            elif "n" in edge:
                fix_point = FixPoint.SOUTH | FixPoint.WEST
            else:
                fix_point = FixPoint.NORTH | FixPoint.WEST
            window.resize(width, height, fix_point)
        return {"resizing": True, "width": width, "height": height}

    def end_resize(self) -> dict[str, bool]:
        with self._lock:
            self._resize_state = None
        return {"resizing": False}

    def _require_window(self) -> Any:
        if self._window is None:
            raise RuntimeError("Desktop window is not ready.")
        return self._window

    def _on_maximized(self) -> None:
        self._maximized = True
        self._notify_window_state()

    def _on_restored(self) -> None:
        self._maximized = False
        self._notify_window_state()

    def _notify_window_state(self) -> None:
        if self._window is None:
            return
        try:
            value = "true" if self._maximized else "false"
            self._window.run_js(f"window.setAgentFarmMaximized?.({value})")
        except Exception:
            # The renderer may already be closing or not loaded yet.
            pass


def _load_webview() -> Any:
    try:
        import webview
    except ImportError as exc:
        raise DesktopDependencyError(
            "Desktop support is not installed. Run: pip install -e \".[desktop]\""
        ) from exc
    return webview


@dataclass
class DesktopRuntime:
    state: ConsoleState
    server: ConsoleHTTPServer
    server_thread: threading.Thread
    _closed: bool = False

    @classmethod
    def start(cls, *, repo_root: Path, config_path: Path | None) -> "DesktopRuntime":
        state = ConsoleState(repo_root=repo_root, config_path=config_path)
        try:
            server = ConsoleHTTPServer(("127.0.0.1", 0), state)
        except Exception:
            state.close()
            raise
        thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.2},
            name="agent-farm-desktop-http",
            daemon=True,
        )
        runtime = cls(state=state, server=server, server_thread=thread)
        thread.start()
        return runtime

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/?desktop=1"

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.server.shutdown()
        self.server.server_close()
        self.state.close()
        if self.server_thread is not threading.current_thread():
            self.server_thread.join(timeout=2)


def run_desktop(
    *,
    repo: Path,
    config_path: Path | None = None,
    width: int = 1440,
    height: int = 900,
    maximized: bool = True,
    debug: bool = False,
) -> None:
    if width < 1040 or height < 700:
        raise ValueError("Desktop window must be at least 1040 x 700.")
    webview = _load_webview()
    repo_root = find_repo_root(repo)
    runtime = DesktopRuntime.start(repo_root=repo_root, config_path=config_path)
    try:
        bridge = DesktopWindowBridge(
            min_width=1040,
            min_height=700,
            initially_maximized=maximized,
        )
        window = webview.create_window(
            "Agent Farm",
            runtime.url,
            js_api=bridge,
            width=width,
            height=height,
            maximized=maximized,
            min_size=(1040, 700),
            resizable=True,
            frameless=True,
            easy_drag=False,
            shadow=True,
            background_color="#171717",
            text_select=True,
        )
        bridge.bind(window)
        window.events.closed += runtime.close
        # WinForms can ignore its pre-show maximized flag for a frameless window.
        # Window.maximize() waits for pywebview's shown event, so applying it from
        # the startup callback makes the final native state deterministic while
        # retaining Windows' taskbar-safe maximized work area.
        webview.start(
            _enforce_initial_window_state,
            (window, maximized),
            debug=debug,
        )
    finally:
        runtime.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-farm-desktop")
    parser.add_argument(
        "--repo",
        type=Path,
        default=discover_default_repo(),
        help="Repository path.",
    )
    parser.add_argument("--config", type=Path, default=None, help="Config JSON path.")
    parser.add_argument("--width", type=int, default=1440, help="Initial window width.")
    parser.add_argument("--height", type=int, default=900, help="Initial window height.")
    parser.add_argument(
        "--windowed",
        action="store_true",
        help="Start restored instead of using the taskbar-safe maximized work area.",
    )
    parser.add_argument("--debug", action="store_true", help="Enable WebView developer tools.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        run_desktop(
            repo=args.repo,
            config_path=args.config,
            width=args.width,
            height=args.height,
            maximized=not args.windowed,
            debug=args.debug,
        )
    except Exception as exc:
        _report_startup_error(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
