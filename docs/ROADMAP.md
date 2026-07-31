# Agent Farm 路线图

> 状态：开发需求初稿
> 版本：v0.3-draft
> 日期：2026-07-08

## 总体方向

Agent Farm 的目标不是停留在 CLI，而是演进为后台 app / daemon：

```text
后台常驻
  -> Codex 内调用
  -> GPT supervisor 派发 worker
  -> cheap / mid worker 执行
  -> GPT supervisor 审查
  -> 自动 merge
  -> 可随时 go back
```

路线原则：

- 先单 worker 稳定，再多 worker。
- 先后台 daemon 与 Codex bridge，再做漂亮 UI。
- 先 GPT supervisor 审查，再自动 merge。
- 自动 merge 必须先实现 rollback。
- 先记录成本数据，再做复杂成本优化。

## Phase 1：单 Worker MVP 稳定化

目标：让单个 Codex worker 稳定产出可审查 patch。

已具备：

- `init` / `init-local`。
- 单 worker `run`。
- 独立 git worktree。
- configurable model/provider。
- gitignored API key。
- patch、logs、result.json。
- 机器审查。
- 显式 merge。
- cleanup。
- untracked file diff 收集。
- UTF-8 worker 输出兼容。
- `cheap` / `mid` worker profile resolver。
- GPT supervisor Worker Plan contract。
- 多 worker 并行 worktree 调度和聚合 review package。
- 结构化 supervisor decision 记录；机器审查通过不再等同于 supervisor 批准。

待补：

- 更完整的 task spec schema 和 schema 版本迁移。
- worker prompt 模板版本管理。
- result.json schema。
- fake Codex worker 进程级集成测试。
- cost record 基础字段。
- checkpoint 数据结构草案。

验收：

- 单 worker 真实 smoke test 可稳定完成。
- 失败任务不污染主工作区。
- API key 不进 Git。
- 所有测试通过。

## Phase 2：后台 App / Daemon

目标：Agent Farm 可以常驻后台，CLI 退居调试入口。

功能：

- `agent-farm daemon start/status/stop`。
- 本地 task API。
- 后台读取 provider / worker profile 配置。
- 后台管理 run state。
- 后台保存 artifact。
- 后台暴露 status / artifacts / metrics。

验收：

- 启动 daemon 后无需手动运行 `agent-farm run`。
- 后台能接受本地调用并返回 task id。
- CLI 能查询 daemon 状态。
- daemon 崩溃后 run artifact 仍可追踪。

## Phase 3：Codex Bridge

目标：用户在 Codex 内完成主要操作。

候选实现：

- MCP server。
- Slash command。
- Local HTTP bridge。
- Stdio bridge。

最低能力：

- Codex 发起 worker run。
- Codex 查询任务状态。
- Codex 获取 review package。
- Codex 发送 supervisor decision。
- Codex 触发 rollback。

验收：

- 用户无需离开 Codex 即可启动 worker。
- Codex 能拿到 diff/log/test summary。
- Codex 能让后台继续执行 merge 或 rollback。

## Phase 4：GPT Supervisor Spec 与 Review

目标：把高级 GPT 的“脑力”变成稳定 contract。

功能：

- 结构化 worker plan。
- 结构化 worker spec。
- 结构化 supervisor review decision。
- request revision flow。
- reject flow。
- hold for user flow。

验收：

- cheap worker 不接收完整主对话。
- worker spec 足够明确，可独立执行。
- GPT review 只基于 evidence package。
- 未通过 GPT approval 不允许 auto merge。

## Phase 5：Auto Merge With Go Back

目标：GPT 审查通过后自动合并，但用户随时能回退。

功能：

- merge 前 checkpoint。
- rollback record。
- reverse patch。
- `go back` 调用入口。
- rollback history。
- dirty worktree 安全拒绝。

验收：

- `approve_merge` 后后台自动 merge。
- merge 前一定有 checkpoint。
- 用户可以回退最近一次 Agent Farm merge。
- 回退失败不会覆盖用户改动。

## Phase 6：Codex 内 Worker Farm

目标：用户可指定 worker 数量、角色和模型档位。

目标体验：

```text
/farm workers=3 roles=explorer:cheap,implementation:mid,reviewer:cheap
```

能力：

- worker count。
- worker role。
- worker profile。
- profile -> model/provider 解析。
- 并行 worktree。
- 汇总报告。
- 多 diff 比较。

验收：

- 单个 Codex 指令可启动多个 worker。
- 每个 worker 的模型来源可追踪。
- 每个 worker 独立保存 patch/log/test。
- GPT supervisor 可以选择合并哪个 patch 或请求返工。

## Phase 7：Worker Roles

角色：

- `explorer`：定位代码、总结方案，默认不改代码。
- `implementation`：实现 patch。
- `tester`：补测试或运行验证。
- `reviewer`：看 diff 和日志，输出风险报告。

验收：

- role-specific prompt。
- role-specific allowed paths。
- role-specific model profile。
- role-specific timeout。
- explorer summary 可作为 implementation 输入。

## Phase 8：成本统计与运行指标

目标：判断 worker farm 是否真的省钱、稳定。

基础指标：

- task id。
- worker role。
- profile。
- model。
- provider。
- duration。
- retry count。
- changed files。
- diff lines。
- test status。
- review status。
- merge status。

进阶指标：

- input tokens。
- output tokens。
- tool calls。
- estimated cost。
- pass rate。
- average revision count。
- cost per accepted patch。

验收：

- 每次 run 有 metrics record。
- 可以按 task / worker / profile 汇总。
- 成本估算可为空，但字段必须存在。

## Phase 9：受控自动化扩展

在 auto merge + rollback 稳定后，可以逐步扩大自动化范围。

低风险候选：

- 文档更新。
- 类型补全。
- 单元测试补充。
- 小范围 bugfix。
- lint 修复。
- 无行为变化重构。

禁止默认自动化：

- 权限。
- 支付。
- 数据库。
- 部署。
- 安全逻辑。
- 大规模重构。

## 暂不考虑

- 商业化。
- 多项目管理。
- 团队账号体系。
