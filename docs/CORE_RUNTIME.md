# Core Runtime Notes

Agent Farm is built around the part of an agent product that users feel when a task is long,
expensive, interrupted, or wrong: a durable execution runtime. The native WinUI client is only a
client of that runtime. A task must remain inspectable and controllable even when the window is
closed.

## What the reference implementations teach us

### Codex

The open-source Codex core treats a turn as a sequence of model requests and tool executions. A
tool call is not an opaque callback: it has a typed request, an approval/sandbox decision, a result,
and an event that the UI can render. Its process runner owns the child process, bounds its resources,
and terminates the complete process group on cancellation or timeout. Streaming model deltas and
tool lifecycle events are part of the product contract, not a logging afterthought.

### DeepSeek Harness

DeepSeek Harness separates durable session facts from live coordination hooks. `turn/*`, `step/*`,
and model/tool messages are appended to an event-sourced session log. Live `agent/*`, `llm/*`, and
`tools/*` hooks can observe, reject, or rewrite work while the durable log remains the replay source
of truth. Its background Job Registry gives long-running producers one lifecycle: owner-scoped
lookup, bounded wait, kill, completion, and failure-isolated listeners. Subagents are child sessions
with explicit lineage rather than untracked threads.

## Agent Farm mapping

| Runtime concern | Agent Farm implementation |
| --- | --- |
| Model/tool turn loop | `agent_farm/native_agent.py` and `agent_farm/model_client.py` |
| Typed incremental events | `EventWriter`, model delta events, and the SQLite event ledger |
| Durable task boundary | `agent_farm/task_runtime.py` |
| Farm scheduling and Worker isolation | `agent_farm/farm.py` and `agent_farm/orchestrator.py` |
| Job and session persistence | `agent_farm/runtime_store.py` and `agent_farm/session_ledger.py` |
| Child-session lineage and authorization | `agent_farm/subagents.py` |
| Resource-bounded process execution | `agent_farm/sandbox.py` |
| Provider/model/harness separation | `agent_farm/harnesses.py`, `routing.py`, and `config.py` |

The executable path is deliberately independent of the UI:

```text
POST /api/tasks
  -> TaskRuntime creates job + session
  -> Supervisor plans with read-only tools
  -> worker-plan.json is persisted
  -> Farm snapshots the usable workspace and creates Worker worktrees
  -> Workers run native or Codex-compatible harnesses
  -> machine review validates evidence and scope
  -> expensive Supervisor reviews or synthesizes
  -> SQLite events and result artifacts remain replayable
```

Every Worker receives a separate worktree. Before the worktrees are created, Agent Farm creates an
alternate-index Git snapshot of the current workspace so uncommitted implementation files are part
of the actual execution context without changing the user's branch or staging area. The snapshot
commit and its source commit are both recorded in the farm result.

## Recovery semantics

Task, farm, plan, and session records have explicit queued, running, cancelling, terminal, and
interrupted states. Startup reconciliation marks active records from a previous daemon as
`INTERRUPTED` and appends a durable recovery event. `GET .../events?after=N` replays the ordered
cursor from SQLite; SSE is only a delivery mechanism over that cursor.

When a stopped task is resumed, a valid persisted `worker-plan.json` is reused. The premium
Supervisor is not called again merely because the desktop or daemon restarted. If the checkpoint is
missing or invalid, the runtime performs a fresh planning pass and records the new plan.

Cancellation reaches the model request, tool loop, sandbox runner, and farm scheduler through one
event. Windows command processes are assigned to a Job Object where available; cancellation,
timeout, output limits, and failed assignment all terminate and close the child process handles.

## Current boundary

The runtime does not claim that model output is correct. It guarantees observable lifecycle,
bounded capabilities, reproducible evidence, and a final review boundary. Semantic correctness,
provider availability, and high-impact decisions still require human review.
