# Agent Farm product kernel plan

## Product rule

Agent Farm is a desktop command center in which an expensive Supervisor plans, routes, reviews, and decides while economical Workers execute narrow tasks in isolated worktrees. The interface is only useful when every visible control is backed by persistent state and an observable runtime action.

## Research findings

The Codex desktop product is organized around projects, persistent threads, typed activity items, worktrees, reviewable diffs, approvals, and long-running background work. Its app-server architecture separates the desktop shell from the thread manager and agent runtime. That separation lets navigation remain responsive while work continues or a backend operation fails.

Agent Farm should follow the same product boundary:

```text
Desktop shell
  -> local app API
     -> thread manager and event log
        -> Supervisor runtime
           -> Worker scheduler
              -> isolated worktrees and model providers
```

Official references used for this direction:

- https://openai.com/index/introducing-the-codex-app/
- https://openai.com/index/unlocking-the-codex-harness/
- https://openai.com/codex/get-started/

## Delivery order

### P0: Reliable application shell

- Navigation binds before any network or repository hydration.
- Every main view has a stable URL hash and supports Back/Forward.
- Settings opens immediately, even when the local API is unavailable.
- Loading and failure states are rendered inline with retry actions.
- Keyboard navigation includes `Ctrl+N` for a task and `Ctrl+,` for Settings.
- Static UI contracts verify that navigation targets and controls exist.

### P1: Runtime-ready Settings

- Validate and atomically persist all editable settings.
- Verify Codex binary, provider endpoint shape, and credential presence.
- Distinguish saved configuration from the effective configuration of an active run.
- Add a provider connection test that never exposes a secret to the renderer.

### P2: Live Thread runtime

- Make Thread, Turn, and Item the only source of truth for the renderer.
- Stream ordered events instead of polling independent job objects.
- Represent messages, plans, commands, approvals, diffs, tests, and decisions as typed Items.
- Recover active work after the desktop app restarts.

### P3: Supervision and approvals

- Pause Workers on command, file, or network approval requests.
- Show the exact requested action, scope, and risk before approval.
- Resume or reject without losing the Turn.
- Keep the Supervisor decision separate from Worker execution.

### P4: Review workspace

- Add first-class terminal output, changed-file navigation, diff review, test evidence, and artifacts.
- Support comments and revision requests from the same Thread.
- Add explicit checkout, apply, merge, and rollback checkpoints.

### P5: Model economics and operations

- Record model, tokens, latency, retries, and estimated cost per Item and Worker.
- Enforce routing and budget policies before execution.
- Add background schedules, notifications, skills, and a review queue.

## Release gate

A feature is not complete when its page is visible. It is complete only when it can be opened from a clean launch, survives API failure, persists its state, reports errors in the relevant view, and has an automated contract or integration test.
