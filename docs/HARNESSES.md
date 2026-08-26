# Agent Farm Harness Contract

Implementation status: Phase 0, Phase 1, Phase 2, and Phase 3 are complete. Phase 4 will add the
external JSON-RPC/JSONL adapter on top of the same runtime contract.

Agent Farm separates four concepts:

```text
role      = why the agent exists (Supervisor or Worker)
harness   = how the agent loop runs (native or Codex compatibility)
provider  = which model service is called
model     = which model is selected
route_id  = stable provider/model identity recorded in artifacts
```

The separation lets a premium Supervisor and economical Workers use different models without
coupling model routing to the execution engine.

## Capability Contract

Every harness publishes a redacted descriptor through `GET /api/harnesses` and the bootstrap and
settings payloads:

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
    "cancellation",
    "resumability"
  ],
  "transports": ["in_process", "http"],
  "supports": {
    "spawn": true,
    "fork": false,
    "continuation": true,
    "structured_output": true,
    "images": true,
    "remote_workspace": false
  },
  "available": true,
  "ready": true
}
```

The runtime checks required capabilities before model traffic begins. A harness must either
implement a requested capability or fail the run clearly; it must not silently downgrade tool,
approval, cancellation, or write behavior.

Current implementations:

| Harness | Purpose | Current contract |
| --- | --- | --- |
| `native` | Agent Farm's in-process model/tool loop | Streaming, tool calls, workspace writes, approval requests, cancellation, resumability |
| `codex` | External Codex-compatible process bridge | Tool calls and workspace writes; captured process output is normalized into Agent Farm JSONL events |

## Stable Event and Artifact Identity

Agent events and farm artifacts carry these fields when an agent is involved:

- `harness_id`: the execution engine that owned the run;
- `route_id`: stable `provider/model` identity for the selected model route;
- `provider_id` and `model_id`: canonical route identifiers;
- `provider` and `model`: backwards-compatible display-friendly aliases;
- `event_seq` and `sequence`: the per-agent event counter and its compatibility alias;
- `agent_kind`: `supervisor` or `worker`;
- `session_id`: the run/session identity;
- `parent_session_id`: the owning session used for child authorization and report scoping.

Native events already use the shared JSONL envelope. Codex stdout is preserved as a raw diagnostic
file and converted to the same envelope in `worker-events.jsonl` or `supervisor-events.jsonl` after
the compatibility process exits. Native remains the live-streaming harness. The SQLite session
ledger stores both harness event streams before the native WinUI client (or optional developer
console) publishes them.

## Child Sessions

`ChildSessionService` provides a harness-neutral `spawn`, capability-gated `fork`, `resume`,
`cancel`, `interrupt`, and bounded `report` lifecycle. It records the selected provider/model route,
permission policy, environment allowlist, and parent lineage. The default child environment removes
credential-like variables; callers must explicitly allow a provider key.

Named child providers are exposed alongside harness descriptors as `native.subagent` and
`codex.subagent`. Their `spawn`, `fork`, and continuation support is derived from the harness
capability descriptor rather than from a second model registry.

## Configuration

Old configurations remain valid:

```json
{ "agent_backend": "native" }
```

New configurations can choose independently:

```json
{
  "supervisor_harness": "native",
  "worker_harness": "native",
  "worker_profiles": {
    "economy": {
      "harness": "codex",
      "provider": "deepseek",
      "model": "deepseek-chat"
    }
  }
}
```

The profile-level `harness` is applied after the root Worker defaults, so each Worker route can
select its own execution engine. If the new fields are absent, `agent_backend` remains the
fallback.

## Adding a Future Harness

1. Add a stable id to `HARNESS_IDS` and implement its descriptor in `available_harnesses`.
2. Add a runner with the same keyword request shape used by the native and Codex runners.
3. Register the runner in `build_registry`.
4. Normalize external output into the shared event envelope and include the stable metadata.
5. Expose only capabilities that are implemented and add one contract test for descriptor,
   selection, event shape, and artifact shape.
6. Add the id to the desktop harness options only through the API response; do not hard-code a
   second registry in the WinUI client.

The current slice intentionally does not add a plugin marketplace or a second protocol. The next
phase can add JSON-RPC/JSONL without changing the durable session meaning.
