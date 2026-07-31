# Agent Farm competitive product blueprint

Date: 2026-07-29

## Product position

Agent Farm is a local desktop command center for economical multi-agent execution. A high-capability Supervisor owns intent, decomposition, routing, verification, and final decisions. Lower-cost Workers execute bounded tasks in parallel, with isolated state and reviewable evidence.

The goal is not a visual clone of one vendor. The product should combine the strongest publicly documented interaction patterns while preserving Agent Farm's model-economics advantage.

## Public product patterns

| Product | Patterns to adopt | Agent Farm interpretation |
| --- | --- | --- |
| Codex app | Project-scoped threads, parallel agents, worktrees, inline diffs, skills, automations, review queue, permissions | Projects and persistent task threads; isolated Worker worktrees; Changes/Files/Tests review workspace; reusable skills; scheduled tasks; explicit approvals |
| Kimi Work | Work and Chat modes, Goals, Skills, Scheduled Tasks, WebBridge, Projects, local files, Agent Swarm, deliverable previews | Focused Work mode first; long-running Goals; browser tool surface; swarm visualization; artifacts that open inside the right inspector |
| WorkBuddy | Natural-language task creation, autonomous plans, multi-expert execution, task management, result area for outputs/files/changes/preview, MCP and custom skills | One task entry point; visible Supervisor plan; role-based Workers; durable task list; inspector tabs for outputs, files, changes, and preview; extensible tool registry |
| Band | Multi-agent worktrees, agent-first desktop workspace, file tree, editor, and active worktree visibility | Optional code workspace with file tree and editor after the task/runtime foundation is complete |

Public references:

- https://openai.com/index/introducing-the-codex-app/
- https://www.kimi.com/en-cn/help/kimi-work/overview
- https://www.kimi.com/en-cn/help/kimi-work/goal-mode
- https://www.workbuddy.cn/docs/workbuddy/Overview
- https://getband.app/

## Target application map

```text
Projects
  -> Tasks / persistent threads
     -> Supervisor plan and acceptance criteria
     -> Workers / sub-agents
        -> activity timeline and tool calls
        -> isolated worktrees or artifact workspaces
     -> Results
        -> deliverables
        -> files
        -> changes
        -> tests and evidence
        -> preview

Reusable capabilities
  -> Model routes
  -> Skills
  -> Browser tools
  -> Automations
  -> Goals
  -> Settings and permissions
```

## Primary desktop layout

- Left sidebar: New task, Tasks, Goals, Automations, Skills, Browser, Model routes, Settings, Projects, recent tasks, and runtime health.
- Center workspace: task conversation, typed activity timeline, Supervisor plan, approvals, and a persistent composer.
- Right inspector: Plan, Agents, Activity, Files, Changes, Tests, Deliverables, and Preview tabs.
- Status surfaces: current project/branch, execution mode, permission policy, active model route, Worker count, cost, and runtime state.

## Agent Farm differentiator

Every task records two distinct routing layers:

1. Supervisor route: expensive, high-capability model for planning and review.
2. Worker routes: economical models selected by role, risk, and expected task complexity.

The UI must show the selected route and estimated/actual cost per agent. The Supervisor receives synthesized Worker evidence instead of blindly forwarding the entire conversation to every Worker.

## Delivery sequence

1. Desktop correctness: DPI-safe work-area sizing, reliable navigation, Settings, failure states, keyboard shortcuts.
2. Durable task workspace: projects, threads, turns, typed items, restart recovery, live event streaming.
3. Economic routing: provider tests, Supervisor/Worker routes, budgets, token and cost accounting.
4. Multi-agent execution: automatic decomposition, role-based Workers, isolated worktrees, parallel activity, pause/resume/cancel.
5. Review workspace: files, diffs, tests, evidence, deliverables, preview, revision requests, checkout/apply/merge.
6. Reusable capabilities: skills, MCP/tool registry, browser tools, permission policies.
7. Long-running work: Goals, scheduled Automations, background service, notifications, review queue.
8. Safety and recovery: checkpoints, rollback, audit trail, project-scoped permissions.

## Completion rule

A navigation item is added only when its state, runtime action, error handling, persistence, and automated contract are implemented. Visible placeholders do not count as product progress.
