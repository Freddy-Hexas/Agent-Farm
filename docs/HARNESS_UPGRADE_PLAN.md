# Agent Farm Harness Upgrade Plan

> Status: Phase 0, Phase 1, Phase 2, and Phase 3 complete; Phase 4+ planned
> Date: 2026-08-25
> Scope: Agent Farm runtime, protocol, orchestration, recovery, evaluation, and desktop integration

## 1. Executive Summary

Agent Farm already has the most important product idea: a premium Supervisor makes decisions while
economical Workers perform bounded work. The next step is to make that idea a first-class harness
architecture instead of keeping it as a set of configuration fields around the current native loop.

The target is a local Agent Runtime with four independent dimensions:

```text
Harness       = how an agent runs
Provider      = which model service is called
Model         = which model is selected
Role          = why the agent exists in the task
```

For example:

```text
Supervisor:
  harness = codex-app-server
  provider = openai
  model = gpt-5.5
  role = supervisor

Worker:
  harness = native
  provider = deepseek
  model = deepseek-v4
  role = researcher
```

This separation is the central architectural upgrade. It lets Agent Farm combine Codex, DeepSeek,
ACP, local models, and future harnesses without confusing a model route with an execution engine.

The plan borrows the following public ideas:

- DeepSeek Harness: capability seams, append-only event-sourced sessions, subagent providers,
  explicit permission boundaries, and resumable child sessions.
- Codex App Server: Thread / Turn / Item primitives, typed JSON-RPC, generated schemas, event
  notifications, cursors, and an app-server control plane.
- Lite Harness: one client contract for multiple agent harnesses and streaming errors/messages.
- Community harness projects: record/replay cassettes, completion gates, governance audits, and
  holdout evaluations.

The plan does **not** propose copying DeepSeek's Cordis framework or replacing Agent Farm's Python
and WinUI stack with the Rust or TypeScript implementation of another product.

## 2. Research Basis

The following projects were reviewed as architectural references:

| Reference | Useful idea for Agent Farm | What not to copy directly |
| --- | --- | --- |
| [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) | Everything is composed through capability seams; sessions are durable event logs; subagents are named providers. | Cordis and the complete TypeScript plugin framework. |
| [DeepSeek session subsystem](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/session.md) | Messages are derived from an append-only log; replay and persistence share one source of truth. | Its unreleased internal event format. |
| [DeepSeek subagent subsystem](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/subagent.md) | `spawn`, `fork`, continuation, lineage authorization, capability validation, and explicit cancellation. | Provider-specific implementation details. |
| [DeepSeek ACP adapter](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/acp/acp/README.md) | JSON-RPC stdio bridge, one-shot permission policy, clean process disposal, and clear automation limits. | ACP as the only user-facing transport. |
| [Codex App Server](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md) | Stable control plane, Thread / Turn / Item lifecycle, generated schema, notifications, cursors, and backpressure. | The Rust runtime itself. |
| [Lite Harness](https://github.com/LiteLLM-Labs/lite-harness) | Unified SDK shape for switching harnesses and models while preserving streaming semantics. | Its preview API and provider assumptions. |
| [Agent behavior harness](https://github.com/nderman/agent-harness) | Record/replay cassettes, deterministic offline tests, trajectory evaluation, and reports. | Its scenario model without adapting it to Worker evidence. |
| [Codex Harness MCP](https://github.com/chapzin/codex-harness-mcp) | Local governance policy, raw traces, PASS / FLAG / BLOCK audits, and completion gates. | Community-specific MCP storage conventions. |
| [VS Code agent harnesses](https://github.com/microsoft/vscode-docs/blob/main/docs/agents/run/agent-harnesses.md) | Harness handoff and explicit isolation choices between folder and worktree. | VS Code-specific UI terminology. |

## 3. Current Baseline

Agent Farm already provides these foundations:

- native WinUI desktop client and local daemon;
- HTTP + SSE protocol v1;
- persistent threads, turns, typed items, durable jobs, and reconnect cursors;
- Supervisor planning and final review;
- parallel Workers with explicit profiles and model/provider routes;
- isolated Git worktrees and deterministic machine review;
- approvals, cancellation, checkpoints, rollback, diagnostics, and crash recovery;
- streaming model deltas, usage records, provider health, and cost estimates;
- sandboxed file, command, and public web capabilities.

The main structural gap is not a missing button. It is that the following concepts are currently
partly mixed together:

1. `agent_backend` describes an execution implementation but is not a registry or capability
   contract.
2. `model_providers` and `worker_profiles` describe routes but do not fully identify the harness
   that owns a run.
3. `threads.py` stores product conversation state while `runtime_store.py` stores job events; the
   two projections can drift because neither is the complete event-sourced source of truth.
4. Worker execution is durable at the farm/job level, but there is no general child-session contract
   for `spawn`, `fork`, `resume`, `report`, and parent-authorized interruption.
5. The current protocol is versioned, but client DTOs and server payloads are still maintained as
   separate implementations instead of being generated from one contract.
6. Tests validate many individual modules, but there is no offline trajectory replay or promotion
   gate for changing prompts, routing policies, or harness implementations.
7. Crash recovery detects interrupted runtime sessions, but long context recovery and semantic task
   checkpoints are not yet a first-class Agent Session feature.

## 4. Target Architecture

```text
WinUI Desktop / CLI / External Client
              |
              | HTTP+SSE and JSON-RPC/JSONL adapters
              v
       Agent Farm Runtime API
              |
              +-- Session Store (append-only event ledger)
              +-- Harness Registry
              +-- Route Registry (provider + model + budget)
              +-- Capability / Approval Policy
              +-- Scheduler and Subagent Runtime
              +-- Checkpoint / Compaction Manager
              +-- Replay / Evaluation Runner
              +-- Completion Gate
              |
      +-------+----------+-----------+
      |                  |           |
  Native Harness   Codex Harness   ACP Harness
      |                  |           |
  DeepSeek route    OpenAI route   Any compatible route
      |                  |           |
  Worker Session   Supervisor      External Worker
```

The critical rule is:

> A route chooses a model. A harness owns the agent loop, tools, persistence, permissions, and
> lifecycle.

## 5. Canonical Domain Contracts

### 5.1 Harness descriptor

Every harness implementation must publish a descriptor before it can be selected:

```json
{
  "harness_id": "native",
  "display_name": "Agent Farm Native",
  "version": "1",
  "capabilities": [
    "streaming",
    "tool_calls",
    "workspace_write",
    "approval_requests",
    "cancel",
    "resume"
  ],
  "transports": ["in_process", "http"],
  "supports": {
    "spawn": true,
    "fork": false,
    "continuation": true,
    "structured_output": true,
    "images": true,
    "remote_workspace": false
  }
}
```

Rules:

- A request requiring an unsupported capability is rejected before a run is created.
- A harness must not silently ignore a requested capability.
- The descriptor is safe to expose to the desktop; secrets and provider endpoints are not included.
- Adding a capability is backward compatible. Removing one requires a protocol or harness version
  change.

### 5.2 Route reference

The selected model route is recorded independently:

```json
{
  "route_id": "worker.deepseek.economy",
  "provider_id": "deepseek",
  "model_id": "deepseek-v4",
  "reasoning_effort": "default",
  "capability_tier": "economy",
  "budget_usd": 0.50,
  "fallback_route_ids": ["worker.qwen.local"]
}
```

The UI and every trace must display both `harness_id` and `route_id`. A label such as `cheap` is a
profile name, not a model identity and must never be shown as the model name.

### 5.3 Session and lineage

Every Supervisor, Worker, review, and synthesis operation receives its own session:

```json
{
  "session_id": "session-01J...",
  "parent_session_id": "session-supervisor-01J...",
  "farm_id": "farm-01J...",
  "role": "researcher",
  "harness_id": "native",
  "route_id": "worker.deepseek.economy",
  "workspace": {
    "kind": "git_worktree",
    "path": ".agent-farm/worktrees/worker-a"
  },
  "status": "running"
}
```

The parent relationship is an authorization boundary. A client can interrupt, steer, or inspect a
child only when it owns the relevant parent session or has an explicit user-level authority.

### 5.4 Append-only event envelope

```json
{
  "schema_version": 1,
  "event_id": "event-01J...",
  "event_seq": 42,
  "event_type": "model.delta",
  "session_id": "session-01J...",
  "parent_session_id": "session-supervisor-01J...",
  "turn_id": "turn-01J...",
  "correlation_id": "request-01J...",
  "created_at": "2026-08-21T12:00:00Z",
  "payload": {
    "text": "The latest evidence shows..."
  }
}
```

The event ledger is the source of truth. Threads, live timelines, usage reports, and final result
views are projections. A projection may be rebuilt from the ledger after a crash or schema
migration.

### 5.5 Completion gate

```json
{
  "verdict": "PASS",
  "contract_present": true,
  "required_outputs_present": true,
  "worker_evidence_complete": true,
  "verification_passed": true,
  "supervisor_approved": true,
  "unresolved_approvals": 0,
  "unresolved_risks": [],
  "generated_at": "2026-08-21T12:30:00Z"
}
```

`PASS` allows the configured next action. `FLAG` pauses for user confirmation. `BLOCK` prevents
merge or delivery until the missing evidence is resolved.

## 6. Phased Implementation Plan

### Phase 0 - Contract inventory and compatibility guard

**Status: Complete (2026-08-25)**

**Goal:** Freeze the vocabulary before changing runtime behavior.

Tasks:

- [x] Inventory every existing `agent_backend`, provider, profile, job, thread, turn, item, and
  event field.
- [x] Define canonical names: `harness_id`, `provider_id`, `model_id`, `route_id`, `session_id`,
  `parent_session_id`, `event_seq`, and `stop_reason`.
- [x] Add a compatibility mapper for old `agent_backend`, `worker_provider`, and `worker_model`
  settings.
- [x] Define protocol v1 additive fields without breaking existing desktop clients.
- [x] Add focused contract fixtures for native, Codex, and failed Worker result shapes.

Files likely involved:

- `agent_farm/models.py`
- `agent_farm/config.py`
- `agent_farm/protocol.py`
- `tests/test_config.py`
- `tests/test_protocol.py`

Exit criteria:

- Old local configuration still loads unchanged.
- New identifiers are present in sanitized bootstrap and job results.
- Unknown security-sensitive fields are still rejected.

### Phase 1 - Harness Registry

**Status: Complete (2026-08-25)**

**Goal:** Replace the binary native/Codex switch with a capability-aware registry.

Tasks:

- [x] Add `HarnessDescriptor`, `HarnessRegistry`, and the shared run dispatch seam.
- [x] Register `native` as the first production implementation.
- [x] Register `codex` as a compatibility harness behind the same interface.
- [x] Add a provider-independent harness selection step before model routing.
- [x] Validate required capabilities before creating a Worker session.
- [x] Record `harness_id` and `route_id` in Supervisor, Worker, and synthesis artifacts.
- [x] Expose `GET /api/harnesses` with redacted descriptors.

Suggested internal API:

```python
class HarnessProvider(Protocol):
    def describe(self) -> HarnessDescriptor: ...
    def start(self, request: HarnessStartRequest) -> HarnessRun: ...
    def cancel(self, run_id: str) -> None: ...
    def resume(self, session_id: str) -> HarnessRun: ...
```

Exit criteria:

- A Worker can select `harness_id` and `route_id` independently.
- A missing capability fails before model traffic starts.
- Existing native and Codex workflows produce equivalent final result contracts.

Implementation notes:

- `native` retains streaming, tool calls, workspace writes, approvals, cancellation, and
  resumability in its descriptor.
- `codex` is selected through the same registry and its process output is normalized into the
  native JSONL event envelope. It reports only capabilities the compatibility bridge currently
  implements; unsupported approval/cancellation semantics are not silently claimed.
- Existing `agent_backend` configurations are still accepted. Explicit `supervisor_harness`,
  `worker_harness`, and per-profile `harness` values override that legacy field.
- Every normalized event carries `harness_id`, `route_id`, canonical `provider_id`/`model_id`,
  backwards-compatible provider/model aliases, role, and session id;
  every run/farm artifact repeats the identifiers needed to interpret it without live settings.

### Phase 2 - Unified Session Event Ledger

**Status: Complete (2026-08-25)**

**Goal:** Make one append-only ledger authoritative for conversation and runtime state.

Tasks:

- [x] Add a SQLite event table with a unique `(session_id, event_seq)` constraint.
- [x] Persist every model, tool, approval, Worker, usage, and lifecycle event.
- [x] Add a projection layer that rebuilds thread timelines and job summaries from events.
- [x] Add `GET /api/sessions/{id}/events?after=<seq>` for replay and reconnect.
- [x] Preserve current thread APIs as projections for compatibility.
- [x] Add event schema version and migration tests.
- [x] Make event writes durable before publishing SSE (the Phase 4 JSON-RPC adapter will consume
  the same ledger).

Required invariants:

- Event sequence numbers are contiguous per session.
- A completed event cannot be rewritten; corrections are new events.
- Every child event carries its parent session when one exists.
- Every external request has a correlation ID.
- A projection can be deleted and rebuilt without losing user-visible history.

Implementation notes:

- Runtime schema version 3 adds `runtime_sessions` and `session_events`; the old job event tables
  remain available for v1 clients and are mirrored in the same SQLite transaction.
- Every session event has both the canonical `event_seq` and the compatibility `sequence` alias,
  an event id, correlation id, lineage fields, and a schema version.
- Existing JSON thread files are migrated into the ledger on daemon startup. `ThreadStore.events`
  and `ThreadStore.rebuild` project the old Thread/Turn/Item shape back from ledger events.
- Job registries append to SQLite before notifying their SSE condition, so a reconnect cursor never
  depends on browser memory.

Exit criteria:

- [x] Rebuild the task timeline only from the event ledger.
- [x] Reconnect from a cursor without duplicated or missing events.
- [x] Persist and reopen a session ledger across store instances.

The full process-kill recovery scenario remains covered by the existing runtime interruption path;
semantic checkpoint compaction is intentionally deferred to Phase 5.

### Phase 3 - Subagent Capability Seam

**Status: Complete (2026-08-25)**

**Goal:** Turn Worker creation into a reusable child-agent service.

Tasks:

- [x] Add named subagent providers to the harness registry.
- [x] Implement `spawn` for a fresh child context.
- [x] Implement `fork` from a completed parent turn when the selected harness supports it.
- [x] Add `resume`, `cancel`, `interrupt`, and `report` operations.
- [x] Enforce parent-session lineage authorization.
- [x] Make child environment construction credential-scrubbed by default.
- [x] Add explicit one-shot permission policy for unattended Workers.
- [x] Return a stable `stop_reason` such as `completed`, `cancelled`, `max_turns`, `blocked`, or
  `failed`.

Implementation notes:

- `ChildSessionService` is the provider-neutral seam. It validates harness capabilities before
  creating a child and stores the requested role, route, permission policy, environment allowlist,
  and parent lineage in the session descriptor.
- `spawn` creates a fresh context; `fork` is capability-gated and only runs for harnesses that
  advertise `supports.fork`; native and Codex keep their current truthful capability values.
- `resume`, `cancel`, and `interrupt` are durable state transitions. `report` returns bounded final
  evidence and counters rather than the raw parent transcript.
- All existing Farm/Plan job events are assigned a session. Worker harness events keep their own
  session id and point back to the owning Farm or Supervisor session as `parent_session_id`.
- Child environments remove credential-like variables by default. A provider-specific key can only
  be passed through an explicit allowlist on the child request.

Exit criteria:

- [x] Supervisor/Farm sessions can own independent Worker child sessions and routes.
- [x] One Worker can fail or be cancelled without corrupting its siblings.
- [x] A Worker report contains final evidence without leaking the full Supervisor transcript.
- [x] A non-owner cannot interrupt another session.

### Phase 4 - JSON-RPC / JSONL App Server Adapter

**Goal:** Make Agent Farm usable by clients other than its own desktop UI.

Tasks:

- [ ] Add a stdio JSONL transport with one request or notification per line.
- [ ] Implement `initialize` / `initialized` handshake.
- [ ] Implement `thread/start`, `thread/read`, `thread/resume`, `thread/fork`.
- [ ] Implement `turn/start`, `turn/interrupt`, and `turn/completed` notifications.
- [ ] Implement `item/started`, item-specific deltas, and `item/completed`.
- [ ] Generate JSON Schema and C# / TypeScript client types from canonical definitions.
- [ ] Keep HTTP + SSE as the native desktop transport.
- [ ] Add overload errors and client retry guidance.

Exit criteria:

- A minimal external JSONL client can create a thread, stream a turn, cancel it, and resume it.
- The same event sequence is observable through SSE and JSON-RPC.
- Protocol tests reject uninitialized or malformed requests deterministically.

### Phase 5 - Context Checkpoints and Compaction

**Goal:** Make long-running tasks resumable at semantic checkpoints, not just process checkpoints.

Tasks:

- [ ] Track request context size and provider context-window metadata.
- [ ] Add a durable task checkpoint containing the current plan pointer, completed Workers,
  pending approvals, artifacts, and next action.
- [ ] Add a compaction event family: `compaction/started`, `compaction/summary`,
  `compaction/completed`.
- [ ] Trigger compaction after successful calls when pressure is high.
- [ ] Retry context-window failures only after a bounded compaction attempt.
- [ ] Never discard the latest plan pointer or accepted Worker evidence.
- [ ] Expose checkpoint state in the desktop timeline and diagnostics export.

Exit criteria:

- A long task can resume after daemon restart without repeating completed Workers.
- A context overflow becomes a recoverable event or an explicit terminal failure.
- The compacted context is auditable and linked to the source event range.

### Phase 6 - Record / Replay Evaluation Harness

**Goal:** Test agent behavior deterministically without spending API budget on every test.

Tasks:

- [ ] Add a cassette format for model responses, tool calls, approvals, and timing metadata.
- [ ] Add `agent-farm eval record` for selected real runs.
- [ ] Add `agent-farm eval replay` with no network and no provider credentials.
- [ ] Add scenario fixtures for research, code modification, failed Worker, cancellation, and
  approval denial.
- [ ] Compare trajectories, not only final text.
- [ ] Record token usage, estimated cost, wall time, retry count, and evidence completeness.
- [ ] Add baseline and candidate harness profiles.
- [ ] Add holdout cases so prompt or routing changes cannot optimize only the visible fixtures.

Suggested evaluation dimensions:

| Dimension | Example assertion |
| --- | --- |
| Routing | Worker uses DeepSeek route; Supervisor uses premium route. |
| Safety | Forbidden path write is denied and logged. |
| Recovery | A pre-output stream reset retries; a partial stream is not duplicated. |
| Collaboration | Supervisor synthesis cites all successful Worker evidence. |
| Efficiency | Candidate reduces cost or latency without lowering evidence completeness. |
| Reproducibility | Replay produces the same state transitions and completion verdict. |

Exit criteria:

- CI can run the core evaluation suite offline.
- A harness change requires a baseline comparison and an explicit promotion note.
- A regression in safety or completion evidence blocks promotion.

### Phase 7 - Governance Completion Gate

**Goal:** Prevent Agent Farm from claiming completion based on a fluent final message alone.

Tasks:

- [ ] Define task contract fields: objective, deliverables, acceptance checks, allowed paths,
  forbidden actions, and review policy.
- [ ] Generate a deterministic PASS / FLAG / BLOCK report.
- [ ] Require raw event trace, verification result, and artifact manifest for PASS.
- [ ] Require Supervisor approval before patch apply or merge.
- [ ] Make unresolved approval or missing evidence a BLOCK.
- [ ] Store gate results with farm and session artifacts.
- [ ] Render the gate in the desktop review surface.

Exit criteria:

- No automatic merge occurs without a PASS gate and Supervisor approval.
- The user can inspect why a result was FLAG or BLOCK.
- The gate can be evaluated offline from stored artifacts.

### Phase 8 - Desktop and External Handoff

**Goal:** Expose the new runtime without making the desktop more confusing.

Tasks:

- [ ] Show Harness, Provider, Model, Role, Sandbox, and Approval policy separately.
- [ ] Add an Agent Session inspector with parent/child lineage.
- [ ] Add a compact event timeline with filtering by model, tool, Worker, and approval.
- [ ] Add “handoff to harness” for supported sessions.
- [ ] Show route fallback and escalation decisions explicitly.
- [ ] Add session export and replay entry points to diagnostics.
- [ ] Keep advanced protocol and governance details behind an inspector, not the main composer.

Exit criteria:

- A user can tell which model did each piece of work.
- A user can cancel one Worker without cancelling the farm.
- A user can resume a session after restarting the desktop.
- UI state is driven by durable events rather than browser-only memory.

## 7. Protocol Evolution and Compatibility

The current HTTP protocol v1 must remain usable during the migration.

### Additive v1 fields

The following fields can be added to existing payloads without breaking old clients:

- `harness_id`
- `route_id`
- `session_id`
- `parent_session_id`
- `stop_reason`
- `capabilities`
- `completion_gate`

### New v2 surface

Use a new protocol version when changing event sequence semantics or replacing existing endpoint
meaning. Candidate v2 resources:

```text
GET  /api/harnesses
GET  /api/sessions
POST /api/sessions
GET  /api/sessions/{id}
GET  /api/sessions/{id}/events
POST /api/sessions/{id}/spawn
POST /api/sessions/{id}/interrupt
POST /api/sessions/{id}/cancel
POST /api/sessions/{id}/resume
GET  /api/sessions/{id}/report
GET  /api/sessions/{id}/children
GET  /api/evals/{id}
POST /api/evals/replay
```

The WinUI client can continue using the current farm endpoints until the new session projection is
stable.

## 8. Security Model

The harness upgrade must preserve and extend the current fail-closed posture.

### Required boundaries

- Harness descriptors are public metadata; credentials and provider URLs are not.
- Child processes receive a scrubbed environment plus explicitly allowed variables only.
- Parent lineage is required for child interruption, continuation, and report delivery.
- A capability request is validated before a run starts.
- Network, filesystem, process, and approval events are recorded with capability manifests.
- Replay never contacts a provider and never executes repository commands unless explicitly enabled
  by a test fixture.
- Untrusted repository content is represented as evidence, not as runtime policy.
- Automatic approval is a harness deployment policy, not a model instruction.

### Threats to test

- A Worker tries to access a sibling Worker's worktree.
- A provider endpoint or API key is written into a trace.
- A child attempts to interrupt a non-child session.
- A replay fixture attempts to execute a command.
- An unknown event type causes a projection to invent semantics.
- A stale client submits an approval decision for a completed request.

## 9. Observability and Cost Metrics

Every session and Worker should report:

- harness, provider, model, role, and profile;
- start and end timestamps;
- input, cached input, output, and total tokens;
- estimated cost and price-catalog version;
- tool calls by capability;
- retries and fallback route changes;
- approvals requested, allowed, denied, or cancelled;
- changed files and diff lines;
- test and machine-review results;
- completion-gate verdict;
- recovery, compaction, and replay counters.

Useful product metrics:

```text
cost_per_accepted_farm
cost_per_successful_worker
supervisor_to_worker_cost_ratio
worker_revision_rate
evidence_completeness_rate
approval_denial_rate
context_recovery_success_rate
replay_determinism_rate
```

The most important business metric is not raw token savings. It is:

```text
accepted deliverables / total model cost
```

## 10. Acceptance Scenarios

The following scenarios define the product-level completion bar.

### Scenario A - Mixed-cost research farm

- Supervisor uses a premium OpenAI route.
- Two Workers use DeepSeek economy routes.
- Each Worker receives only its task brief and permitted sources.
- Worker events stream independently.
- Supervisor synthesis cites both Worker evidence.
- The final report is written to the requested output directory.
- The completion gate is `PASS` only when the artifact exists and is readable.

### Scenario B - Mixed harness coding task

- Supervisor runs through the native harness.
- A code Worker runs through Codex App Server.
- A test Worker runs through the native harness with a local model.
- Each Worker has a separate worktree.
- One Worker fails; the other result remains available.
- No patch reaches the main workspace without review and approval.

### Scenario C - Crash and resume

- Kill the daemon while a Worker is streaming.
- Restart the daemon.
- Rebuild the timeline from the event ledger.
- Resume from the last durable checkpoint.
- Do not duplicate already committed model or tool events.

### Scenario D - Permission denial

- A Worker requests a forbidden file write.
- The request appears as a durable approval event.
- The user denies it.
- The Worker receives a structured denial result and continues or fails according to policy.
- The completion gate records the denial.

### Scenario E - Offline replay

- Record a real run once.
- Disable network and remove provider credentials.
- Replay the run.
- Verify identical routing, tool, approval, and completion-gate transitions.

## 11. Risks and Deliberate Non-Goals

### Risks

- Unifying `threads.py` and `runtime_store.py` can create migration bugs if projections are changed
  before event fixtures exist.
- Supporting multiple harnesses increases process lifecycle and permission complexity.
- Context compaction can hide important evidence if the checkpoint contract is underspecified.
- Replay tests can create false confidence if they only compare final text.
- JSON-RPC compatibility can become expensive if the protocol is expanded without generated schemas.

### Non-goals for this upgrade

- Rewriting the desktop in another UI framework.
- Replacing Python orchestration with Rust or TypeScript.
- Implementing every third-party provider's native SDK.
- Building a hosted multi-tenant control plane.
- Enabling unrestricted automatic approval.
- Adding a large plugin marketplace before the capability contract is stable.
- Optimizing token context with external graph tools before measurements exist.

## 12. First Implementation Slice

The first code increment should be deliberately narrow:

1. Add `HarnessDescriptor`, `HarnessRegistry`, and `harness_id` to runtime contracts.
2. Register the current native loop without changing its behavior.
3. Add `harness_id`, `route_id`, `session_id`, and `parent_session_id` to Worker and Supervisor
   results.
4. Add `GET /api/harnesses` and a desktop read-only harness display.
5. Add contract tests for native and Codex compatibility routes.
6. Add one mixed-cost smoke fixture: premium Supervisor plus DeepSeek economy Worker.

This slice proves the most important architectural property before any risky event-store migration:

> The same task can select a premium Supervisor model and an economical Worker model while the
> execution engine remains independently replaceable.

## 13. Definition of Done for the Overall Upgrade

- [x] Harness, provider, model, and role are separate persisted concepts.
- [x] Sessions are reconstructable from one append-only event ledger.
- [x] Subagents support explicit spawn, fork, resume, cancel, and report semantics.
- [ ] HTTP/SSE and JSON-RPC/JSONL expose the same durable event meaning.
- [ ] Context checkpoints prevent long-running tasks from restarting semantically.
- [ ] Offline replay covers routing, tools, approvals, recovery, and completion gates.
- [ ] PASS / FLAG / BLOCK is enforced before automatic apply or merge.
- [ ] Desktop clearly shows which harness and model performed each action.
- [ ] Existing v1 configuration and current desktop workflows remain compatible.
- [ ] Security, protocol, migration, replay, and mixed-cost smoke tests pass in CI.
