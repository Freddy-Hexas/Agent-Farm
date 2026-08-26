# Agent Farm Desktop App

## Product direction

Agent Farm Desktop is a local command center for a high-capability supervisor and a pool of lower-cost execution agents. Its defining product rule is visible model separation:

```text
expensive supervisor = understand + plan + review + decide
cheap/mid workers    = implement + test + return evidence
```

The desktop app must never disguise every model call as one generic "AI". The task composer, Worker Plan, live run, and review package all show which side of the cost boundary is active.

## Patterns adopted from current desktop agents

Research was limited to official product pages and documentation:

- [OpenAI Codex app](https://openai.com/index/introducing-the-codex-app/): projects organize parallel agent threads; agents use isolated worktrees; diffs are reviewed in context; skills and scheduled work have dedicated surfaces.
- [Kimi Work](https://www.kimi.com/zh-sg/help/kimi-work/overview): natural-language goals are the primary entry point; Work and Chat are distinct; local files, skills, scheduled tasks, browser work, and Agent/Agent Cluster modes are first-class concepts.
- [Tencent WorkBuddy task bar](https://www.codebuddy.ai/docs/zh/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Task-Bar): Ask/Craft/Plan modes make execution authority explicit; model and working directory are task-level choices; each conversation owns an independent workspace.
- [Tencent WorkBuddy task management](https://www.codebuddy.ai/docs/zh/workbuddy/Task-Management): recent tasks and workspaces live in the left rail; status, search, resume, archive, and folder access are persistent task-management operations.

Agent Farm adopts the shared information architecture without copying product branding or exact visual assets:

1. Stable left rail for new work, run history, model routes, and recent tasks.
2. Natural-language mission composer as the primary action.
3. A visible Supervisor planning phase before execution.
4. An editable Worker Plan showing profile, scope, tests, and acceptance criteria.
5. Live status for parallel workers.
6. Evidence-first review containing changed files, test results, machine findings, and patch content.
7. Explicit Supervisor decisions; workers never merge directly.

## Desktop architecture

The product shell is a real Windows application built with .NET 8, Windows App SDK, WinUI 3, and XAML. The `AgentFarm.Desktop` project owns the window, title bar, navigation, settings, task composer, execution inspector, and review surfaces. It is the same native client shipped in the MSIX; the source launcher and the packaged release use the same project.

Python owns the local orchestration runtime only. The native client starts or reconnects to the repository daemon and talks to it through a loopback HTTP + SSE protocol. No browser, WebView, HTML page, or JavaScript bundle is used to render the product UI. Closing the native window hides it while the durable daemon and active jobs keep running; launching the app again reopens the existing window when the process is still alive.

The Windows shell uses native title-bar integration, taskbar-safe maximization, resizable navigation and execution panes, keyboard focus, Automation IDs, and WinUI resource dictionaries. `Start-AgentFarm.cmd` and `agent-farm desktop` both launch this WinUI path. The optional `agent-farm ui` command is retained only as a developer compatibility console and is not part of the desktop product or release package.

```text
native WinUI 3 desktop
  -> typed loopback HTTP + SSE
     -> repository daemon
        -> Supervisor planner
        -> Farm scheduler and route registry
        -> Worker sessions and isolated Git worktrees
        -> artifact, review, and event stores
```

The interaction model follows the same separation used by Codex app-server, while keeping the existing Python runtime:

```text
desktop shell
  -> task thread
     -> user turn
        -> items: supervisor plan, worker activity, test evidence, diff, decision
  -> local app service
     -> expensive Supervisor (plan and review)
     -> economical Worker pool (isolated execution)
     -> Git worktrees and artifact store
```

The current HTTP API is the local transport boundary. UI code does not import orchestration code or provider SDKs directly. A later transport can add JSON-RPC or WebSocket event streaming without changing the Thread / Turn / Item interaction model.

## Extensibility boundary

DeepSeek Harness-style extensibility belongs in the runtime, not in a browser shell. Agent Farm keeps
the following replaceable contracts behind the native client:

- harness registry: native, Codex-compatible, and future harness adapters;
- provider and model catalog: premium Supervisor routes and economical Worker routes;
- tool, skill, approval, sandbox, and session capabilities;
- durable thread, event, artifact, and diagnostic records;
- typed HTTP + SSE today, with JSONL/JSON-RPC available to future clients.

The WinUI client renders those contracts as first-class native surfaces. New harnesses, providers, and
skills therefore extend the product without replacing the desktop shell or adding a WebView.

## Codex-style desktop information architecture

The desktop UI intentionally behaves as a work surface instead of an analytics dashboard:

1. The left rail owns projects, recent task threads, history, and model routes.
2. The center is a persistent task thread with user turns, Supervisor planning, Worker activity, and a bottom composer.
3. The right inspector owns diffs, test evidence, machine findings, and the final Supervisor decision.
4. Planning and execution remain separate turns. The user can edit the generated Worker Plan before execution.
5. Model separation stays visible in every task: expensive Supervisor decisions and economical Worker execution are never presented as the same generic model call.

Security properties:

- non-loopback binding is rejected;
- static asset routes are allowlisted;
- JSON request bodies and artifact reads are size-limited;
- cross-origin mutations are rejected;
- farm and worker identifiers are validated;
- artifact paths must remain inside the repository;
- bootstrap returns redacted model metadata, never provider endpoints or secrets;
- Supervisor planning uses a read-only sandbox and does not inherit Worker provider configuration.

## Settings Center

Settings are a real runtime boundary, not a presentation-only page. The desktop app reads the effective Agent Farm configuration and persists validated changes to the gitignored `agent-farm.local.json` layer. Saved settings apply to new Supervisor and Worker runs.

The current settings surfaces cover:

- expensive Supervisor model, Codex profile, and planning timeout;
- economical Worker routes with independent model, provider, reasoning effort, and timeout;
- custom Responses-compatible provider endpoints and environment-variable credential status;
- sandbox, approval policy, network access, ephemeral sessions, and lockfile policy;
- worktree, run artifact, and farm evidence directories;
- parallelism, execution timeouts, and machine-review patch limits;
- desktop appearance and Codex executable health.

Provider secret values are never returned by the settings API. Existing literal header or query configuration is preserved during a UI save but remains hidden. API keys continue to live in the configured secrets env file.

## Persistent Thread model

Desktop tasks are persisted under `.agent-farm/threads` using a Codex-style hierarchy:

```text
Thread
  -> Turn
     -> Item: user_message
     -> Item: supervisor_plan
     -> Item: farm_run
     -> Item: supervisor_decision
```

Supervisor planning and Worker execution update the same Turn instead of existing only in browser memory. Each state transition also appends a monotonically ordered event. The local API can create and read Threads and request events after a known sequence number, providing the compatibility layer for subsequent realtime streaming.

## Current scope

The desktop slice includes native window lifecycle, a persistent Settings Center, persistent Thread / Turn storage, natural-language Supervisor planning, editable Worker Plans, background Farm execution, run history, evidence inspection, patch viewing, and structured Supervisor decisions.

Realtime event streaming, interactive approvals, installer generation, native notifications, OS file pickers, scheduled tasks, skills management, automatic merge checkpoints, and rollback UI remain subsequent product increments.
