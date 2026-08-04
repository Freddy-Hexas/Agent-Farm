# Agent Farm architecture

This document describes the architecture shipped in Agent Farm 0.5.0.9. The primary client is a
native WinUI 3 application; the optional browser console is a separate compatibility surface and
is never embedded in the desktop window.

## Design objective

Agent Farm separates judgment from execution. A high-capability Supervisor plans, routes, reviews,
and synthesizes. Economical Workers perform bounded tasks concurrently. Each Worker route names a
provider, model, reasoning mode, capability tier, and optional budget, so a Worker never silently
inherits the Supervisor model.

```text
Native WinUI client
       |
       | typed loopback HTTP + SSE, protocol v1
       v
Desktop runtime daemon
       |
       +-- threads, jobs, approvals, usage, checkpoints, diagnostics
       +-- Supervisor planner and final reviewer
       +-- farm scheduler and routing policy
       |
       +---- Worker A / economical model / isolated Git worktree
       +---- Worker B / economical model / isolated Git worktree
       +---- Worker N / local or hosted model / isolated Git worktree
                          |
                          v
                 patch + tests + evidence
```

## Native desktop layer

`AgentFarm.Desktop` targets .NET 10, Windows App SDK, WinUI 3, and XAML. Its main surfaces are split
into native user controls under `AgentFarm.Desktop/Views`: workspace, settings, providers, runs,
review, and live execution. View models expose observable state and commands; the code-behind owns
Windows integrations, navigation, long-running API calls, and lifecycle coordination.

The desktop application:

- starts maximized inside the monitor work area, preserving the Windows taskbar;
- uses resizable navigation and execution panes;
- exposes keyboard focus, Automation IDs, localized resources, notifications, and recovery states;
- starts the Python source daemon in Debug and the frozen `AgentFarmBackend.exe` in Release;
- carries `X-Correlation-ID` on every API request;
- renders model deltas, tool activity, Worker status, review evidence, and durable history without a
  WebView.

Release builds are self-contained for .NET and Windows App SDK. The frozen Python backend is copied
after WinUI resource indexing so Python ABI filenames cannot pollute the PRI index.

## Runtime and orchestration layer

The loopback runtime is implemented by `agent_farm/web_server.py` and bound only to a local port.
`agent_farm/desktop_server.py` owns the single-daemon lease and writes a runtime descriptor used by
the desktop client. A source fingerprint prevents Debug builds from attaching to a stale daemon.

Core responsibilities are separated by module:

| Module | Responsibility |
| --- | --- |
| `supervisor.py` | Plan creation, final review, and collaborative synthesis |
| `farm.py` | Parallel scheduling, routing, budgets, cancellation, and farm results |
| `orchestrator.py` | Worker lifecycle, worktree creation, execution, and evidence collection |
| `native_agent.py` | Multi-turn model/tool loop and bounded tool dispatch |
| `model_client.py` | Responses and Chat Completions adapters with incremental deltas |
| `routing.py` | Explicit capability-, provider-, and model-aware Worker selection |
| `change_control.py` | Candidate patch inspection, apply, merge, and rollback boundaries |
| `runtime_store.py` | Durable SQLite jobs, ordered events, correlation IDs, and reconnect cursors |
| `threads.py` | Persistent threads, turns, typed items, and event history |
| `approvals.py` | Durable command, file-write, and network approval requests |

## Task lifecycle

1. The desktop creates or resumes a thread and uploads attachment copies into the repository-local
   attachment store.
2. `POST /api/plans` queues a durable planning job. The Supervisor emits incremental output and a
   validated plan.
3. `POST /api/farms` queues execution. The scheduler resolves each named Worker profile and creates
   an isolated worktree from the requested base ref.
4. Workers run concurrently, emitting ordered model, tool, usage, approval, and status events.
5. Machine review checks path boundaries, change size, lockfiles, test deletion, and configured
   verification commands.
6. Passing Worker change sets are presented for explicit apply/merge or used as evidence for final
   synthesis. A Worker never writes directly into the Supervisor workspace.

Planning and farm jobs are durable. The client reconnects with an event cursor after a window or
daemon interruption. Cancellation is explicit and scoped to a plan, farm, or Worker.

## Storage and recovery

Repository-local product state lives under `.agent-farm/` and is ignored by Git. It includes the
runtime descriptor, SQLite runtime database, thread history, farm artifacts, worktrees, approvals,
diagnostics, and backups. Editable provider routes live in the ignored `agent-farm.local.json`;
credentials live in `.agent-farm/secrets.env` or the process environment.

On startup, a session marker detects an unclean previous exit. Active jobs are reconciled into a
recoverable terminal state and the recovery report is returned by bootstrap. SQLite and config
backups use retention limits and SHA deduplication. Diagnostic export creates a sanitized ZIP with
configuration, runtime summary, correlated backend logs, and structured desktop events.

## Trust boundaries

- The desktop talks only to its loopback daemon and explicitly approved provider endpoints.
- Secrets are write-only through Settings and are redacted from logs and diagnostic exports.
- Worker commands use a no-shell allowlist and a restricted environment.
- In `workspace-write`, repository code and tests run from a sanitized copy in a Docker container
  with no network, a read-only root filesystem, dropped capabilities, and resource limits. If the
  daemon or image is unavailable, execution fails closed.
- `allowed_paths` and `forbidden_paths` are enforced before and after execution.
- Network tools require configured access and can generate a durable approval.
- Production packaging requires a protected CA-issued certificate and a trusted timestamp.

See [Protocol](PROTOCOL.md), [Security](SECURITY.md), and [Current limitations](LIMITATIONS.md).
