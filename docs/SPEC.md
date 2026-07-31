# Agent Farm SPEC 总览

> 状态：开发需求初稿
> 版本：v0.3-draft
> 日期：2026-07-08

本目录将 Agent Farm 的开发需求拆成三份文档：

- [PRODUCT_SPEC.md](./PRODUCT_SPEC.md)：产品定位、用户交互、功能边界、验收口径。
- [TECH_SPEC.md](./TECH_SPEC.md)：后台 app/daemon、Codex 调用桥、worker orchestration、自动合并与回退机制。
- [ROADMAP.md](./ROADMAP.md)：阶段计划、worker farm、Codex 内调度入口、成本统计与 rollback 能力。

## 当前共识

- README 暂不修改。
- 暂不加入商业化章节。
- 暂不加入多项目管理章节。
- 加入成本统计，但先作为工程指标，不做商业化计费。
- Agent Farm 的长期形态不是用户手动操作的 CLI，而是类似 `cc switch` 的后台 app / daemon。
- 用户正常使用时主要留在 Codex 内，通过 slash command、MCP、local bridge 或其他 Codex 入口调用 Agent Farm。
- Codex 里的高级 GPT 模型是 supervisor brain，负责理解需求、拆任务、写精确 worker spec、审查结果和触发合并。
- 后台 cheap / mid worker 使用什么模型、provider、API key，由 Agent Farm 后端配置决定。
- Worker 不能直接 merge。Worker 只能产出 patch、日志、测试结果和风险说明。
- 通过机器审查后，必须由 GPT supervisor 审查 patch。GPT supervisor 审查通过后可以自动 merge，不要求用户再次确认。
- 自动 merge 必须创建 checkpoint / rollback record。用户需要可以随时在 Codex 中要求 `go back` 回退。

## 目标架构

```text
User
  -> Codex GPT Supervisor
     - understands request
     - writes precise worker specs
     - selects worker count / roles / model tiers
     - reviews evidence
     - approves auto merge
     - can request rollback

  -> Codex bridge
     - slash command / MCP / local bridge / future integration

  -> Agent Farm Background App / Daemon
     - provider and API key config
     - worker pool
     - task state
     - artifact store
     - machine review
     - checkpoint and rollback manager
     - cost metrics

  -> Codex Worker Processes
     - cheap / mid models
     - isolated git worktrees
     - local implementation
     - tests and logs
     - patch only

  -> GPT Supervisor Review
  -> Auto merge with checkpoint
  -> Go back / rollback available
```

## 当前开发主线

短期：继续稳定单 worker MVP、本地 provider 配置和已落地的异构模型 farm 纵向切片。
中期：实现后台 app / daemon 与 Codex 内调用入口。
长期：实现可指定 worker 数量、角色、模型档位的 worker farm，并支持 GPT 审查后的自动 merge 与可回退 checkpoint。
