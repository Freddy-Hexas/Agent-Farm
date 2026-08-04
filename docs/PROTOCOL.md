# Desktop runtime protocol

Agent Farm 0.5.0.9 uses loopback HTTP protocol version `1`. The native client discovers the daemon
from `.agent-farm/runtime.json`, calls `/api/protocol`, and initializes a session before relying on
optional capabilities.

## Compatibility contract

The server publishes supported versions, capabilities, and JSON Schemas. Protocol initialization
fails if there is no shared version or a required capability is unavailable.

Current capabilities are:

- `approvals.v1`
- `attachments.v1`
- `cancellation.v1`
- `durable-jobs.v1`
- `model-deltas.v1`
- `reconnect-cursor.v1`
- `typed-messages.v1`

```http
POST /api/protocol/initialize
Content-Type: application/json
X-Correlation-ID: 6f4f14d4-0f04-4a46-a432-f786a7a43a03

{
  "client_name": "AgentFarm.Desktop",
  "client_version": "0.5.0.9",
  "protocol_versions": [1],
  "capabilities": ["model-deltas.v1", "reconnect-cursor.v1"],
  "required_capabilities": ["durable-jobs.v1", "typed-messages.v1"]
}
```

Every request may carry `X-Correlation-ID`; the server validates and returns it. If omitted, the
server creates one. The ID is persisted on jobs and structured logs.

## Primary endpoints

| Method and path | Purpose |
| --- | --- |
| `GET /api/health` | Runtime health, protocol, source fingerprint, and process state |
| `GET /api/protocol` | Versions, capabilities, and schema identifiers |
| `GET /api/protocol/schemas` | Full protocol v1 JSON Schemas |
| `POST /api/protocol/initialize` | Negotiate a client session |
| `GET /api/bootstrap` | Threads, active jobs, settings summary, and recovery report |
| `GET/POST /api/settings` | Read sanitized settings or save editable routes |
| `GET /api/providers/{id}/models` | Provider model catalog; `?refresh=1` bypasses cache |
| `POST /api/attachments` | Copy a local file into the bounded attachment store |
| `GET/POST /api/threads` | List or create threads |
| `GET /api/threads/{id}` | Read a complete typed thread |
| `POST /api/threads/{id}/{action}` | `rename`, `archive`, `resume`, `fork`, or `delete` |
| `POST /api/plans` | Queue Supervisor planning and return a plan-job descriptor |
| `GET /api/plan-jobs/{id}` | Read planning state and result |
| `GET /api/plan-jobs/{id}/stream` | Stream planning events over SSE |
| `POST /api/farms` | Queue an accepted plan for Worker execution |
| `GET /api/jobs/{id}` | Read farm-job state and result |
| `GET /api/jobs/{id}/stream` | Stream farm and Worker events over SSE |
| `POST /api/jobs/{id}/cancel` | Cancel a farm |
| `POST /api/jobs/{id}/retry` | Retry the farm or one `worker_id` |
| `GET /api/farms/{id}/review-package` | Deterministic review summary and evidence |
| `GET /api/farms/{id}/changesets` | Candidate Worker change sets |
| `GET /api/farms/{id}/workers/{worker}/patch` | Bounded unified diff |
| `GET/POST /api/approvals` | List approvals or resolve an approval decision |
| `POST /api/diagnostics/export` | Create a sanitized diagnostic ZIP |
| `POST /api/runtime/stop` | Gracefully stop the owned daemon |

The code is the authoritative endpoint definition. Unknown request fields are rejected on security-
sensitive operations to detect client/server drift early.

## Durable jobs and streaming

Planning and farm creation return `202 Accepted` with a job ID. Events are assigned monotonically
increasing sequence numbers and stored in SQLite before delivery. A client can use either:

- SSE: `GET .../stream` with `Last-Event-ID`; or
- polling: `GET .../events?after=<sequence>`.

The stream includes lifecycle events, Supervisor/Worker messages, incremental `model.delta` text,
tool calls and results, usage updates, approvals, review evidence, cancellation, completion, and
structured failures. Heartbeats keep an idle connection observable. Model inference has no client
HTTP deadline; it ends on model completion, explicit cancellation, or process shutdown.

## Typed messages

Thread items and stream payloads carry `schema_version: 1`. Published schemas cover `thread`,
`turn`, `item`, `worker`, `tool`, `diff`, `approval`, and `usage`. Consumers must ignore unknown
fields in version 1 messages but must not invent semantics for an unknown item `type`.

Errors use an appropriate HTTP status and a JSON error body. Validation errors are client faults;
provider, execution, or runtime faults are recorded on the durable job so reconnecting clients see
the same terminal result.

## Versioning policy

Additive fields and event types can ship within protocol version 1. Removing or changing required
fields, sequence semantics, endpoint behavior, or approval meaning requires a new protocol version
with an overlap period. Database schema migrations are tested against prior fixtures before a
release.
