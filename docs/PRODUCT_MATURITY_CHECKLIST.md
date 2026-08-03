# Agent Farm Product Maturity Checklist

This document is the implementation ledger for evolving Agent Farm from a functional alpha into a reliable desktop agent product.

## Tracking rules

- `[ ]` means the requirement is not fully implemented or not yet verified.
- `[x]` means the implementation, automated tests, and acceptance checks are complete.
- Requirements are completed in order unless a blocking dependency requires a documented exception.
- A feature is not checked merely because UI controls or configuration fields exist; the complete runtime workflow must work.

## Phase 1 - Durable runtime state and restart recovery

- [x] Persist Supervisor planning jobs in SQLite.
- [x] Persist Farm execution jobs in SQLite.
- [x] Persist ordered planning and execution events in SQLite.
- [x] Preserve completed and failed job history across backend restarts.
- [x] Reconcile abandoned `QUEUED` and `RUNNING` jobs as `INTERRUPTED` on startup.
- [x] Reconcile the corresponding Thread, Turn, and Item states after interruption.
- [x] Add migration-safe database initialization and indexes.
- [x] Add unit tests for job persistence, event ordering, filtering, and restart recovery.
- [x] Pass the complete Python test suite.
- [x] Pass the native WinUI build with zero errors.

Acceptance criteria:

- Restarting the Python backend does not remove planning jobs, Farm jobs, or their events.
- Work that was interrupted by process termination no longer remains permanently marked as running.
- Existing repositories without a runtime database open normally and initialize the database automatically.

## Phase 2 - Long-lived Agent Farm daemon

- [x] Move backend ownership out of the WinUI window lifecycle.
- [x] Add daemon start, status, health, and graceful stop operations.
- [x] Add a single-instance lock and stale-lock recovery.
- [x] Let WinUI connect to an existing daemon or start one when necessary.
- [x] Keep work running when the desktop window closes.
- [x] Add backend process health monitoring and automatic reconnection.
- [x] Drain and persist backend stdout/stderr without pipe deadlocks.
- [x] Add protocol and runtime version compatibility checks.
- [x] Add integration tests for close, reopen, crash, and reconnect scenarios.

Acceptance criteria:

- A ten-minute Farm continues after the WinUI window closes.
- Reopening the application reconnects and catches up without losing events.
- A backend crash produces a recoverable state instead of a permanently spinning UI.

## Phase 3 - Typed bidirectional streaming and approvals

- [x] Define versioned Thread, Turn, Item, Worker, Tool, Diff, Approval, and Usage message schemas.
- [x] Add an initialization handshake and capability negotiation.
- [x] Replace 350 ms polling with server-pushed ordered events.
- [x] Support incremental model message deltas.
- [x] Support command, file, and network approval requests initiated by the runtime.
- [x] Pause the active turn until the user allows or denies the requested action.
- [x] Add allow-once, allow-for-session, deny, and cancel decisions.
- [x] Add turn interrupt and worker cancel operations.
- [x] Add reconnect cursors and missed-event replay.
- [x] Add protocol contract and ordering tests.

Acceptance criteria:

- Model output appears incrementally without polling.
- An `on-request` policy always pauses protected actions before execution.
- Reconnection resumes from the last acknowledged event without duplication or loss.

## Phase 4 - Real execution sandbox

- [x] Introduce a `SandboxRunner` abstraction independent of the Worker agent loop.
- [x] Add a restricted Windows runner and a WSL or Docker runner.
- [x] Deny filesystem access outside explicitly granted roots.
- [x] Deny network access by default and support scoped host approvals.
- [x] Restrict child process trees and terminate descendants reliably.
- [x] Enforce CPU, memory, runtime, and output limits.
- [x] Prevent repository test/build scripts from escaping Worker permissions.
- [x] Record an auditable capability manifest for every tool execution.
- [x] Add malicious-repository and sandbox-escape regression tests.

Acceptance criteria:

- A Worker cannot read user credentials or files outside the granted workspace.
- A Worker cannot access the network without an explicit policy grant.
- Test and package-manager scripts receive the same restrictions as built-in tools.

## Phase 5 - Review, checkpoint, apply, merge, and rollback

- [x] Add a typed per-Worker change set and unified diff model.
- [x] Render file diffs, tests, commands, evidence, and Supervisor decisions in WinUI.
- [x] Compare multiple Worker candidates side by side.
- [x] Create a checkpoint before applying any approved change.
- [x] Apply the selected Worker patch from the desktop application.
- [x] Verify the applied result with configured tests.
- [x] Support merge only after Supervisor approval and successful verification.
- [x] Add rollback to the most recent checkpoint.
- [x] Add checkpoint history and cleanup policy.
- [x] Add binary-file and conflicting-patch handling.

Acceptance criteria:

- An approved Farm result can be reviewed, applied, verified, and rolled back entirely from WinUI.
- No automatic merge occurs without a checkpoint and recorded Supervisor approval.

## Phase 6 - Cost-aware Supervisor and Worker routing

- [x] Normalize token usage across supported providers.
- [x] Maintain a versioned model price catalog with custom price overrides.
- [x] Record tokens, latency, retries, and estimated cost for every model request.
- [x] Display separate Supervisor and Worker costs.
- [x] Add per-Worker, per-Farm, and monthly budget limits.
- [x] Add budget warning and hard-stop policies.
- [x] Route simple tasks to the least expensive capable Worker model.
- [x] Retry transient failures without duplicating accepted work.
- [x] Escalate failed or low-confidence Worker tasks to a stronger model.
- [x] Add provider health, rate-limit, fallback, and circuit-breaker policies.
- [x] Give each Worker only the files, attachments, and context required for its task.
- [x] Report cost per accepted artifact and savings versus a single premium-model run.

Acceptance criteria:

- Every Farm provides an auditable quality and cost breakdown.
- Budget policies are enforced by the runtime, not only displayed by the UI.
- The system can demonstrate when economical Workers saved cost and when escalation was necessary.

## Phase 7 - Desktop architecture and user experience

- [x] Split the monolithic `MainPage` into Workspace, Timeline, Composer, Execution, Review, Runs, Settings, and Provider surfaces.
- [x] Move commands, state transitions, and services into testable MVVM components.
- [x] Replace the mostly empty workspace with a typed activity timeline.
- [x] Add Worker DAG, progress, retry, cancel, and failure-recovery controls.
- [x] Add Thread search, rename, archive, delete, resume, and fork operations.
- [x] Add responsive breakpoints and collapsible side panes.
- [x] Add Light, Dark, and High Contrast resource dictionaries.
- [x] Replace hard-coded sizes and colors with Fluent resources.
- [x] Complete keyboard navigation, focus behavior, AutomationId, and AutomationName coverage.
- [x] Move user-facing strings into localization resources.
- [x] Add background notifications and queue visibility.
- [x] Add empty, loading, degraded, offline, and recovery states.

Acceptance criteria:

- Core workflows are keyboard accessible and usable at narrow and wide window sizes.
- Theme changes and High Contrast work without unreadable controls.
- Business logic can be tested without constructing the full WinUI page.

## Phase 8 - Production engineering and distribution

- [x] Add GitHub Actions for Python tests and WinUI builds.
- [x] Run native UI automation in CI on Windows.
- [x] Add provider contract and mocked streaming tests.
- [x] Add database and configuration migration tests.
- [x] Add structured logs, correlation IDs, and a user-exportable diagnostic bundle.
- [x] Add crash detection and recovery reporting.
- [x] Add production certificate signing and timestamping.
- [x] Add a reliable one-click installer and upgrade path.
- [x] Add automatic update checks with a release channel policy.
- [x] Add dependency, secret, and vulnerability scanning.
- [x] Add backup, retention, and cleanup policies for runtime artifacts.
- [x] Keep README, architecture, protocol, and limitations documentation synchronized with releases.

Acceptance criteria:

- Every release is reproducible, tested, signed, installable, and upgradeable.
- A failed production run can be diagnosed without exposing API keys or sensitive file contents.
