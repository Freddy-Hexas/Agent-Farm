# Agent Farm 技术规格说明

> 状态：开发需求初稿
> 版本：v0.3-draft
> 日期：2026-07-08

## 1. 技术目标

Agent Farm 的目标形态是本机后台 app / daemon，而不是只由用户手动调用的 CLI。CLI 是开发和调试入口；正常使用路径应从 Codex 内部进入。

核心技术目标：

- 后台服务常驻运行。
- Codex 可以通过 slash command、MCP、local bridge 或其他机制调用后台。
- GPT supervisor 负责任务拆解、精确 spec、审查和 merge approval。
- Cheap / mid worker 在独立 worktree 中执行。
- Worker 不能 merge。
- GPT approval 后可自动 merge。
- 每次 merge 都有 checkpoint，用户可随时 rollback。

## 2. 总体架构

```text
Codex GPT Supervisor
  -> Codex Bridge
     - slash command
     - MCP server
     - local HTTP / stdio bridge
     - future app/plugin integration

  -> Agent Farm Background App / Daemon
     - task API
     - worker profile resolver
     - orchestrator
     - worker pool
     - artifact store
     - machine review
     - supervisor review handoff
     - merge manager
     - checkpoint / rollback manager
     - metrics and cost tracker

  -> Isolated Git Worktrees
  -> Codex Worker Processes
  -> Diff / Logs / Test Results
  -> GPT Supervisor Review
  -> Auto Merge With Checkpoint
```

## 3. 关键边界

- 用户主要在 Codex 中交互。
- 后台 app 不自行理解用户意图；高级 GPT supervisor 负责理解和拆解。
- worker 不接收完整主对话，只接收精确 worker spec。
- worker 只产出 patch 和证据。
- 机器审查只能 gate 明显风险，不能替代 GPT supervisor。
- 自动 merge 只能由 GPT supervisor approval 触发。
- 自动 merge 必须先创建 rollback checkpoint。

## 4. 后台 App / Daemon

### 4.1 职责

后台进程负责：

- 启动时加载配置。
- 维护 worker profile registry。
- 提供本地调用接口。
- 接收 task plan。
- 创建 run state。
- 创建 git worktree。
- 启动 worker process。
- 收集 artifact。
- 执行机器审查。
- 将 review package 返回给 Codex GPT supervisor。
- 接收 supervisor approval。
- 创建 checkpoint。
- 自动 merge。
- 执行 rollback。
- 记录 metrics 和 cost。

### 4.2 运行方式

目标形态：

```text
agent-farm daemon start
agent-farm daemon status
agent-farm daemon stop
```

或封装成桌面/后台 app，类似 `cc switch`，启动后用户不需要关注它。

### 4.3 CLI 角色

CLI 保留，但定位调整为：

- 初始化配置。
- 调试 provider。
- 本地 smoke test。
- 查看 artifact。
- 手动 cleanup。
- daemon 不可用时 fallback。

## 5. Codex Bridge

Codex Bridge 是 Codex 与后台 daemon 的连接层。具体实现可选：

- MCP server：Codex 调用本地 MCP tools。
- Slash command：Codex 内部命令解析后调用 daemon。
- Local HTTP：daemon 提供 localhost API。
- Stdio bridge：Codex 插件或脚本通过 stdio 调用。

Bridge 至少需要暴露：

- `farm_run`
- `farm_status`
- `farm_review_package`
- `farm_approve_merge`
- `farm_request_revision`
- `farm_rollback`
- `farm_artifacts`
- `farm_metrics`

## 6. GPT Supervisor Contract

GPT supervisor 与 Agent Farm 之间需要结构化 contract。

### 6.1 Worker Plan

```json
{
  "schema_version": 1,
  "task_id": "optional-human-name",
  "workers": [
    {
      "role": "implementation",
      "profile": "cheap",
      "goal": "Implement the requested change.",
      "allowed_paths": ["src/auth", "tests/auth"],
      "forbidden_paths": [".env", ".github/workflows"],
      "test_commands": ["python -m unittest discover"],
      "acceptance": ["New behavior works", "Existing tests pass"]
    }
  ]
}
```

### 6.2 Worker Spec

每个 worker spec 必须包含：

- 任务目标。
- 修改范围。
- 禁止范围。
- 相关上下文摘要。
- 验收标准。
- 测试命令。
- 输出要求。
- 风险边界。

### 6.3 Supervisor Review Decision

```json
{
  "schema_version": 1,
  "decision": "approve_merge",
  "task_id": "...",
  "approved_patch": "patch.diff",
  "risk_level": "low",
  "reason": "Diff is scoped, tests passed, behavior matches task.",
  "rollback_required": true
}
```

允许的 decision：

- `approve_merge`
- `request_revision`
- `reject`
- `hold_for_user`
- `rollback`

## 7. Worker Profile Resolver

后台配置负责把模型档位解析为实际模型和 provider。

示例：

```json
{
  "worker_profiles": {
    "cheap": {
      "model": "cheap-model",
      "provider": "cheap-provider",
      "timeout_seconds": 900
    },
    "mid": {
      "model": "mid-model",
      "provider": "mid-provider",
      "timeout_seconds": 1800
    },
    "reviewer": {
      "model": "review-model",
      "provider": "review-provider"
    }
  }
}
```

用户在 Codex 中只需要说 `cheap` / `mid` / `reviewer`，不用暴露 endpoint 或 API key。

当前实现状态：

- `worker_profiles`、`default_worker_profile` 和 `--profile` 已实现。
- `farm-run` 可解析 GPT supervisor 编写的 Worker Plan，并按 profile 并行运行多个隔离 worker。
- 每个 farm 生成聚合 `review-package.json`，等待高级 supervisor 审查。
- `farm-decide` 只记录结构化决定；checkpoint / rollback 完成前不自动合并。

## 8. 模块职责

### 8.1 `cli.py`

当前 CLI 入口。未来改为 daemon 的调试入口。

### 8.2 `daemon.py`（未来）

后台服务主入口，负责生命周期、API 监听、任务队列和 worker pool。

### 8.3 `bridge_mcp.py` / `bridge_http.py`（未来）

Codex 调用桥。负责把 Codex 请求转换成 daemon task API。

### 8.4 `orchestrator.py`

负责任务生命周期和 worker 调度。

### 8.5 `codex_worker.py`

负责构造并执行 `codex exec`，注入 provider 配置和 secrets env。

### 8.6 `git_ops.py`

负责 repo、worktree、diff、patch、checkpoint 相关 git 操作。

### 8.7 `review.py`

负责机器审查。

### 8.8 `checkpoint.py`（未来）

负责 merge 前 checkpoint、reverse patch、rollback record 和 go back。

### 8.9 `metrics.py`（未来）

负责运行指标和成本统计。

## 9. 状态机

基础任务状态：

```text
CREATED
SPEC_READY
WORKTREE_CREATED
WORKER_RUNNING
WORKER_FINISHED
TESTING
MACHINE_REVIEW_PENDING
MACHINE_REVIEW_PASSED
SUPERVISOR_REVIEW_PENDING
SUPERVISOR_APPROVED
REVISION_REQUESTED
REJECTED
CHECKPOINT_CREATED
MERGED
ROLLBACK_REQUESTED
ROLLED_BACK
ABANDONED
```

关键变化：

- `MACHINE_REVIEW_PASSED` 不代表可以 merge。
- `SUPERVISOR_APPROVED` 才代表 GPT brain 允许 merge。
- `CHECKPOINT_CREATED` 必须出现在 `MERGED` 之前。
- `ROLLED_BACK` 必须保留 rollback evidence。

## 10. Merge 与 Rollback

### 10.1 Merge 前置条件

自动 merge 必须满足：

- worker 成功退出。
- patch 非空。
- 机器审查通过。
- GPT supervisor 给出结构化 `approve_merge`。
- 当前工作区状态可 checkpoint。
- checkpoint 创建成功。

### 10.2 Checkpoint 策略

每次 merge 前必须保存：

- task id。
- base commit。
- pre-merge HEAD。
- pre-merge working tree status。
- patch.diff。
- reverse patch 或可恢复引用。
- merge 时间。
- approving supervisor decision。

可选实现：

- 创建 `refs/agent-farm/checkpoints/<task-id>` 指向 merge 前 HEAD。
- 保存 pre-merge dirty patch。
- 保存 apply 后 reverse patch。
- 如果未来启用 auto-commit，则记录 merge commit。

### 10.3 Rollback 行为

用户在 Codex 中说 `go back` 后：

```text
Codex GPT -> bridge -> daemon -> rollback manager
```

rollback manager 应：

- 找到最近一次 Agent Farm merge。
- 校验当前工作区是否可安全回退。
- 应用 reverse patch 或恢复 checkpoint。
- 写入 rollback log。
- 返回 rollback result 给 Codex。

如果无法安全回退，应停止并返回原因，不得强制覆盖用户未保存改动。

## 11. 机器审查

机器审查必须检查：

- worker 是否失败或超时。
- diff 是否为空。
- changed files 是否超过限制。
- diff lines 是否超过限制。
- 是否越过 allowed paths。
- 是否命中 forbidden paths。
- 是否修改 lockfile。
- 是否删除测试。
- test command 是否失败。

机器审查输出是 supervisor review 的输入，不是最终 merge 决策。

## 12. 配置与密钥

公共配置：

- `agent-farm.config.json`

本地私有配置：

- `agent-farm.local.json`

密钥：

- `.agent-farm/secrets.env`

要求：

- API key 不得进入 Git。
- API key 不得进入 result.json。
- API key 不得进入 patch。
- provider config 可记录 env var 名称，但不能记录 key 本体。

## 13. Artifact

每个 run 保存：

```text
.agent-farm/runs/<task-id>/
  result.json
  worker-prompt.md
  worker-events.jsonl
  worker-stderr.log
  worker-final.md
  patch.diff
  tests/*.log
  review-package.json
  supervisor-decision.json
  checkpoint.json
  rollback.json
```

测试和 smoke artifact 可整体移动到：

```text
.agent-farm/test-bundles/<bundle-id>/
```

## 14. 成本统计

每个 worker run 应记录：

- role。
- profile。
- model。
- provider。
- start time。
- end time。
- duration。
- retry count。
- tool call count，如果可得。
- input tokens，如果可得。
- output tokens，如果可得。
- estimated cost，如果可得。
- merge outcome。

成本统计不用于商业化，只用于判断 worker farm 是否值得、是否稳定。

## 15. 测试策略

必须覆盖：

- config loading。
- local secrets。
- provider override。
- fake worker patch。
- untracked file diff。
- UTF-8 output。
- machine review。
- checkpoint creation。
- rollback happy path。
- rollback dirty-worktree refusal。

合并前至少运行：

```powershell
python -m unittest discover -s tests
git diff --check
```
