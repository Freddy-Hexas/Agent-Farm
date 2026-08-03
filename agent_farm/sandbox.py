from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .review import normalize_path, path_matches
from .util import ensure_inside


class SandboxError(RuntimeError):
    pass


class SandboxUnavailable(SandboxError):
    pass


@dataclass(frozen=True)
class SandboxLimits:
    timeout_seconds: int
    memory_mb: int
    cpus: float
    pids: int
    max_output_chars: int


@dataclass(frozen=True)
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    cancelled: bool
    output_truncated: bool
    manifest: dict[str, Any]


class SandboxRunner(ABC):
    name: str

    @abstractmethod
    def available(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def run(
        self,
        argv: list[str],
        *,
        worktree: Path,
        cwd: Path,
        limits: SandboxLimits,
        cancel_check: Callable[[], bool] | None = None,
    ) -> SandboxResult:
        raise NotImplementedError


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _executable(argv: list[str]) -> str:
    name = Path(argv[0]).name.casefold()
    return name.rsplit(".", 1)[0] if name.endswith((".exe", ".cmd", ".bat")) else name


def command_executes_repository_code(argv: list[str]) -> bool:
    return _executable(argv) not in {"rg", "git"}


def _validate_read_only_host_command(argv: list[str], worktree: Path) -> None:
    executable = _executable(argv)
    if executable == "git":
        if len(argv) < 2 or argv[1] not in {"status", "diff", "show", "log", "ls-files", "grep"}:
            raise SandboxError("Only read-only Git commands can use the Windows sandbox.")
        forbidden = {
            "--config-env",
            "--exec-path",
            "--ext-diff",
            "--git-dir",
            "--no-pager",
            "--paginate",
            "--pathspec-from-file",
            "--textconv",
            "--upload-pack",
            "--work-tree",
        }
        if any(
            argument.split("=", 1)[0] in forbidden
            or "pager" in argument.casefold()
            for argument in argv[2:]
        ):
            raise SandboxError("The Git command requests an external process.")
    elif executable == "rg":
        forbidden = {"--files-from", "--hostname-bin", "--ignore-file", "--pre"}
        if any(argument.split("=", 1)[0] in forbidden for argument in argv[1:]):
            raise SandboxError("The rg command requests an external process.")
    else:
        raise SandboxError("Repository code requires the Docker sandbox.")

    root = worktree.resolve()
    for argument in argv[1:]:
        if not argument or argument.startswith("-"):
            continue
        candidate = Path(argument)
        if candidate.is_absolute():
            try:
                ensure_inside(root, candidate.resolve())
            except ValueError as exc:
                raise SandboxError("Command arguments cannot reference paths outside the worktree.") from exc
        elif ".." in candidate.parts:
            raise SandboxError("Command arguments cannot traverse outside the worktree.")


def _sanitized_environment() -> dict[str, str]:
    allowed = {
        "COMSPEC",
        "NUMBER_OF_PROCESSORS",
        "OS",
        "PATH",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "WINDIR",
    }
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_EXTERNAL_DIFF": "",
        }
    )
    return environment


class _WindowsJob:
    def __init__(self, *, memory_mb: int, cpus: float, pids: int) -> None:
        self.handle: Any = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class BASIC_LIMITS(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class EXTENDED_LIMITS(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC_LIMITS),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class CPU_RATE(ctypes.Structure):
            _fields_ = [("ControlFlags", wintypes.DWORD), ("CpuRate", wintypes.DWORD)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        limits = EXTENDED_LIMITS()
        limits.BasicLimitInformation.LimitFlags = 0x00002000 | 0x00000100 | 0x00000008
        limits.BasicLimitInformation.ActiveProcessLimit = max(1, pids)
        limits.ProcessMemoryLimit = max(64, memory_mb) * 1024 * 1024
        if not kernel32.SetInformationJobObject(
            handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            kernel32.CloseHandle(handle)
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
        cpu_rate = CPU_RATE()
        cpu_rate.ControlFlags = 0x1 | 0x4
        cpu_rate.CpuRate = max(
            1,
            min(10_000, int(float(cpus) / max(1, os.cpu_count() or 1) * 10_000)),
        )
        kernel32.SetInformationJobObject(handle, 15, ctypes.byref(cpu_rate), ctypes.sizeof(cpu_rate))
        self.handle = handle
        self._kernel32 = kernel32

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        if self.handle is None:
            return
        if not self._kernel32.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise OSError("AssignProcessToJobObject failed")

    def terminate(self) -> None:
        if self.handle is not None:
            self._kernel32.TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        if self.handle is not None:
            self._kernel32.CloseHandle(self.handle)
            self.handle = None


def _run_process(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    limits: SandboxLimits,
    cancel_check: Callable[[], bool] | None,
    job: _WindowsJob | None = None,
) -> tuple[int, str, str, bool, bool, bool]:
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        creationflags=creationflags,
    )
    if job is not None:
        job.assign(process)
    buffers: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    totals = {"stdout": 0, "stderr": 0}
    output_limit = max(1, limits.max_output_chars * 4)
    output_exceeded = threading.Event()

    def drain(name: str, stream: Any) -> None:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            totals[name] += len(chunk)
            remaining = output_limit - len(buffers[name])
            if remaining > 0:
                buffers[name].extend(chunk[:remaining])
            if totals["stdout"] + totals["stderr"] > output_limit * 2:
                output_exceeded.set()

    readers = [
        threading.Thread(target=drain, args=("stdout", process.stdout), daemon=True),
        threading.Thread(target=drain, args=("stderr", process.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + limits.timeout_seconds
    timed_out = False
    cancelled = False
    while process.poll() is None:
        if cancel_check is not None and cancel_check():
            cancelled = True
        if time.monotonic() >= deadline:
            timed_out = True
        if cancelled or timed_out or output_exceeded.is_set():
            if job is not None:
                job.terminate()
            else:
                process.kill()
            break
        time.sleep(0.05)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    for reader in readers:
        reader.join(timeout=2)
    stdout = buffers["stdout"].decode("utf-8", errors="replace")
    stderr = buffers["stderr"].decode("utf-8", errors="replace")
    truncated = output_exceeded.is_set() or any(total > output_limit for total in totals.values())
    return process.returncode, stdout, stderr, timed_out, cancelled, truncated


class WindowsRestrictedRunner(SandboxRunner):
    name = "windows-restricted"

    def __init__(self, *, allow_repository_code: bool = False) -> None:
        self.allow_repository_code = allow_repository_code

    def available(self) -> bool:
        return os.name == "nt"

    def run(
        self,
        argv: list[str],
        *,
        worktree: Path,
        cwd: Path,
        limits: SandboxLimits,
        cancel_check: Callable[[], bool] | None = None,
    ) -> SandboxResult:
        if not self.allow_repository_code:
            _validate_read_only_host_command(argv, worktree)
        executable = shutil.which(argv[0], path=_sanitized_environment().get("PATH"))
        if executable is None:
            raise SandboxUnavailable(f"Command is not installed: {argv[0]}")
        try:
            ensure_inside(worktree.resolve(), Path(executable).resolve())
        except ValueError:
            pass
        else:
            raise SandboxError("Executables inside the repository are not trusted.")
        command = [executable, *argv[1:]]
        started_at = _utc_now()
        started = time.monotonic()
        job = _WindowsJob(memory_mb=limits.memory_mb, cpus=limits.cpus, pids=limits.pids)
        try:
            returncode, stdout, stderr, timed_out, cancelled, truncated = _run_process(
                command,
                cwd=cwd,
                environment=_sanitized_environment(),
                limits=limits,
                cancel_check=cancel_check,
                job=job,
            )
        finally:
            job.close()
        manifest = {
            "schema_version": 1,
            "backend": self.name,
            "isolation": "job-object-and-sanitized-environment",
            "workspace": str(worktree.resolve()),
            "filesystem_roots": [str(worktree.resolve())],
            "network": "denied-by-command-allowlist",
            "command": argv,
            "limits": limits.__dict__,
            "started_at": started_at,
            "duration_seconds": round(time.monotonic() - started, 3),
        }
        return SandboxResult(returncode, stdout, stderr, timed_out, cancelled, truncated, manifest)


DOCKER_IMAGES = {
    "python": "python:3.13-slim",
    "python3": "python:3.13-slim",
    "py": "python:3.13-slim",
    "pytest": "python:3.13-slim",
    "ruff": "python:3.13-slim",
    "mypy": "python:3.13-slim",
    "npm": "node:22-bookworm-slim",
    "pnpm": "node:22-bookworm-slim",
    "yarn": "node:22-bookworm-slim",
    "bun": "oven/bun:1",
    "dotnet": "mcr.microsoft.com/dotnet/sdk:10.0",
    "cargo": "rust:1-bookworm",
    "go": "golang:1.25-bookworm",
    "git": "alpine/git:latest",
    "rg": "mstruebing/ripgrep:latest",
}


class DockerSandboxRunner(SandboxRunner):
    name = "docker"

    def __init__(self, *, forbidden_paths: list[str] | None = None) -> None:
        self.binary = shutil.which("docker")
        self._availability: bool | None = None
        self.forbidden_paths = list(forbidden_paths or [])

    def available(self) -> bool:
        if self.binary is None:
            return False
        if self._availability is None:
            try:
                result = subprocess.run(
                    [self.binary, "info", "--format", "{{.ServerVersion}}"],
                    capture_output=True,
                    timeout=3,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                self._availability = result.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                self._availability = False
        return self._availability

    def run(
        self,
        argv: list[str],
        *,
        worktree: Path,
        cwd: Path,
        limits: SandboxLimits,
        cancel_check: Callable[[], bool] | None = None,
    ) -> SandboxResult:
        if not self.available() or self.binary is None:
            raise SandboxUnavailable(
                "Docker isolation is required for repository code. Start Docker Desktop or select a configured secure runner."
            )
        executable = _executable(argv)
        image = DOCKER_IMAGES.get(executable)
        if image is None:
            raise SandboxError(f"No isolated runtime image is configured for {argv[0]}.")
        root = worktree.resolve()
        resolved_cwd = cwd.resolve()
        ensure_inside(root, resolved_cwd)
        relative_cwd = resolved_cwd.relative_to(root).as_posix()
        container_name = "agent-farm-" + uuid.uuid4().hex[:16]
        with tempfile.TemporaryDirectory(prefix="agent-farm-sandbox-") as temporary:
            isolated = Path(temporary) / "workspace"

            def ignore_untrusted(path: str, names: list[str]) -> set[str]:
                directory = Path(path).resolve()
                try:
                    prefix = directory.relative_to(root).as_posix()
                except ValueError:
                    prefix = "."
                ignored = {
                    name
                    for name in names
                    if name in {".git", ".agent-farm", "bin", "obj", "node_modules"}
                    or (Path(path) / name).is_symlink()
                    or any(
                        path_matches(
                            pattern,
                            normalize_path(f"{prefix}/{name}" if prefix != "." else name),
                        )
                        for pattern in self.forbidden_paths
                    )
                }
                return ignored

            shutil.copytree(
                root,
                isolated,
                symlinks=False,
                ignore=ignore_untrusted,
            )
            workdir = "/workspace" + (f"/{relative_cwd}" if relative_cwd != "." else "")
            command = [
                self.binary,
                "run",
                "--rm",
                "--name",
                container_name,
                "--pull=never",
                "--network=none",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--read-only",
                "--pids-limit",
                str(limits.pids),
                "--memory",
                f"{limits.memory_mb}m",
                "--cpus",
                str(limits.cpus),
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=256m",
                "--mount",
                f"type=bind,source={isolated},target=/workspace",
                "--workdir",
                workdir,
                image,
                *argv,
            ]
            started_at = _utc_now()
            started = time.monotonic()
            returncode, stdout, stderr, timed_out, cancelled, truncated = _run_process(
                command,
                cwd=root,
                environment=_sanitized_environment(),
                limits=limits,
                cancel_check=cancel_check,
            )
            daemon_error = stderr.casefold()
            if returncode != 0 and (
                "cannot connect to the docker daemon" in daemon_error
                or "error during connect" in daemon_error
                or "docker desktop is not running" in daemon_error
            ):
                self._availability = False
                raise SandboxUnavailable(
                    "Docker Desktop stopped or became unavailable while the isolated command was starting. Start Docker Desktop and retry the Worker."
                )
            if timed_out or cancelled:
                subprocess.run(
                    [self.binary, "rm", "-f", container_name],
                    capture_output=True,
                    timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            manifest = {
                "schema_version": 1,
                "backend": self.name,
                "isolation": "container-copy",
                "workspace": "/workspace",
                "filesystem_roots": ["/workspace", "/tmp"],
                "network": "none",
                "command": argv,
                "image": image,
                "limits": limits.__dict__,
                "forbidden_paths": list(self.forbidden_paths),
                "started_at": started_at,
                "duration_seconds": round(time.monotonic() - started, 3),
            }
            return SandboxResult(
                returncode,
                stdout,
                stderr,
                timed_out,
                cancelled,
                truncated,
                manifest,
            )


class SandboxManager:
    def __init__(
        self,
        *,
        backend: str,
        sandbox_mode: str,
        memory_mb: int,
        cpus: float,
        pids: int,
        max_output_chars: int,
        forbidden_paths: list[str] | None = None,
    ) -> None:
        self.backend = backend
        self.sandbox_mode = sandbox_mode
        self.memory_mb = memory_mb
        self.cpus = cpus
        self.pids = pids
        self.max_output_chars = max_output_chars
        self.windows = WindowsRestrictedRunner(
            allow_repository_code=sandbox_mode == "danger-full-access"
        )
        self.forbidden_paths = list(forbidden_paths or [])
        self.docker = DockerSandboxRunner(forbidden_paths=self.forbidden_paths)

    def run(
        self,
        argv: list[str],
        *,
        worktree: Path,
        cwd: Path,
        timeout_seconds: int,
        cancel_check: Callable[[], bool] | None = None,
    ) -> SandboxResult:
        limits = SandboxLimits(
            timeout_seconds=timeout_seconds,
            memory_mb=self.memory_mb,
            cpus=float(self.cpus),
            pids=self.pids,
            max_output_chars=self.max_output_chars,
        )
        if self.backend == "docker":
            return self.docker.run(
                argv,
                worktree=worktree,
                cwd=cwd,
                limits=limits,
                cancel_check=cancel_check,
            )
        if self.backend == "windows":
            if self.forbidden_paths:
                raise SandboxError(
                    "The Windows command runner cannot prove forbidden-path isolation. Use Docker or remove host-command access."
                )
            return self.windows.run(
                argv,
                worktree=worktree,
                cwd=cwd,
                limits=limits,
                cancel_check=cancel_check,
            )
        if self.sandbox_mode != "danger-full-access":
            return self.docker.run(
                argv,
                worktree=worktree,
                cwd=cwd,
                limits=limits,
                cancel_check=cancel_check,
            )
        return self.windows.run(
            argv,
            worktree=worktree,
            cwd=cwd,
            limits=limits,
            cancel_check=cancel_check,
        )
