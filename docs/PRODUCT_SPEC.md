# Agent Farm 产品需求说明

> 状态：开发需求初稿
> 版本：v0.3-draft
> 日期：2026-07-08

## 1. 产品定位

Agent Farm 是一个面向 Codex 的后台 worker orchestration app。它最终应像 `cc switch` 一类工具一样在本机后台安静运行，负责模型配置、worker 调度、worktree 隔离、artifact 保存、机器审查、自动合并和回退。

用户的主要交互界面不是 Agent Farm CLI，而是 Codex。Codex 里的高级 GPT 模型是 supervisor brain；Agent Farm 是后台执行层；便宜或专用模型是 worker。

一句话：

```text
用户在 Codex 里提出需求，高级 GPT 精确拆任务并调用后台 Agent Farm；Agent Farm 调度便宜 worker 产出 patch；GPT 审查通过后自动合并，并保留随时 go back 的能力。
```

## 2. 背景与问题

当前多 agent coding workflow 的主要问题不是“模型不会写代码”，而是缺少工程边界和产品化入口：

- 用户不希望频繁切换到命令行手动调度 worker。
- cheap worker 的效果高度依赖 GPT supervisor 给出的任务 spec 是否准确。
- worker 直接修改主工作区会带来污染和回滚风险。
- worker 自述不可靠，必须看 diff、测试日志、修改范围和风险证据。
- 自动合并如果没有 checkpoint，会让用户不敢放权。
- worker 模型、provider、API key 需要后台配置，不能散落在 prompt 或代码里。

Agent Farm 的产品价值是把“便宜模型参与开发”变成一个可控的后台执行系统。

## 3. 核心用户体验

### 3.1 正常使用都在 Codex 内完成

用户应能在 Codex 中通过 slash command、MCP、local bridge 或未来官方集成入口发起 worker farm：

```text
/farm workers=3 roles=explorer:cheap,implementation:mid,reviewer:cheap
```

也可以用自然语言：

```text
用 2 个便宜 worker 帮我分别做实现和测试，完成后你检查并合并。
```

Codex GPT supervisor 将请求翻译为结构化 worker plan，并交给后台 Agent Farm。

### 3.2 GPT supervisor 是大脑

高级 GPT 模型负责：

- 理解用户需求。
- 判断任务风险。
- 拆分 worker 任务。
- 编写精确 worker spec。
- 选择 worker 数量、角色和模型档位。
- 审查 worker 的 diff、测试日志、风险报告。
- 决定 approve / revise / reject。
- 审查通过后触发自动 merge。
- 在用户要求时触发 rollback。

### 3.3 Worker 是执行者

便宜 worker 模型负责：

- 在独立 worktree 中读代码。
- 按 spec 做局部修改。
- 运行允许的测试命令。
- 产出 patch、日志和最终说明。

Worker 不负责：

- 决定架构方向。
- 扩大任务范围。
- 直接 merge。
- push。
- 修改 secrets 或部署配置，除非明确允许。

### 3.4 自动合并但必须可回退

目标形态下，用户不需要每次手动确认 merge。流程应是：

```text
worker 完成
  -> machine review pass
  -> GPT supervisor review pass
  -> Agent Farm 创建 checkpoint
  -> 自动 merge
  -> 保存 rollback record
```

用户可以随时在 Codex 中说：

```text
go back
```

或：

```text
回退上一次 Agent Farm 合并
```

Agent Farm 应恢复到上一个 checkpoint 或应用 reverse patch。

## 4. 目标用户

### 4.1 Codex 用户

主要用户是在 Codex 中完成日常开发的人。他们希望高级 GPT 负责判断与审查，后台 worker 负责低成本执行。

### 4.2 本机配置者

该用户负责配置 worker provider、API key、本地模型、模型档位、默认 worker profile 和安全策略。

### 4.3 未来团队用户

未来可支持团队约定 worker profile，例如 `cheap-test`、`mid-backend`、`reviewer`。当前阶段不做多项目管理，也不做团队权限系统。

## 5. 产品目标

### 5.1 当前阶段目标

- 单 worker MVP 稳定运行。
- 支持本地 provider、模型和 API key 配置。
- API key 不进入 Git。
- Worker 在独立 worktree 中执行。
- Worker 只产出 patch，不 merge。
- 支持机器审查。
- 支持人工/CLI merge 作为早期调试入口。

### 5.2 目标阶段目标

- Agent Farm 可以作为后台 app / daemon 运行。
- Codex 内部可以调用 Agent Farm。
- Codex GPT 可以指定 worker 数量、角色和模型档位。
- 后台根据配置解析实际模型和 provider。
- GPT supervisor 审查通过后自动 merge。
- 每次自动 merge 都能 rollback。
- 记录成本与运行指标。

### 5.3 非目标

当前不做：

- 商业化。
- 多项目管理。
- 团队账号系统。
- 替代 Codex CLI 的 agent loop。
- cheap worker 自主 merge。
- 高风险任务默认自动合并。

## 6. 功能需求

### 6.1 后台 app / daemon

系统应支持常驻后台运行。后台进程负责：

- 读取本机配置。
- 管理 worker profiles。
- 接收 Codex 调用请求。
- 创建任务和 worktree。
- 运行 worker。
- 保存 artifact。
- 执行机器审查。
- 等待 GPT supervisor 审查决策。
- 执行 merge 和 rollback。

CLI 保留为开发、调试和 fallback 入口。

### 6.2 Codex 调用入口

系统应预留以下入口之一或多个：

- slash command。
- MCP server。
- local HTTP / stdio bridge。
- Codex plugin / app integration。

第一版不强制确定最终入口，但 SPEC 要求所有核心能力都能被 Codex 内调用，而不是只面向人工 CLI。

### 6.3 Worker Spec

Worker spec 是系统成功的核心。GPT supervisor 必须给 cheap worker 提供高精度任务说明：

- 任务目标。
- 允许修改路径。
- 禁止修改路径。
- 上下文摘要。
- 验收标准。
- 测试命令。
- 输出格式。
- 风险边界。

Spec 应尽量短、明确、可验证，不能把完整主对话无脑塞给 worker。

### 6.4 Worker Farm

用户可指定：

- worker 数量。
- worker 角色。
- 模型档位。
- 是否并行。
- 是否需要 reviewer worker。

示例：

```text
workers=3
roles=explorer:cheap,implementation:mid,reviewer:cheap
```

实际模型映射由后台配置决定。

### 6.5 审查与自动合并

合并条件：

- worker 进程成功。
- patch 非空。
- 机器审查通过。
- 测试命令通过，或 GPT supervisor 明确接受无测试风险。
- GPT supervisor 基于 diff/logs/result.json 输出结构化 approval。
- checkpoint 创建成功。

满足条件后，Agent Farm 可自动 merge，不需要用户确认。

### 6.6 Go Back / Rollback

系统必须支持：

- 查看最近 merge 记录。
- 回退上一次 Agent Farm merge。
- 回退指定 task id。
- 回退失败时报告原因。
- 回退操作本身保存日志。

回退目标不是“永久不出错”，而是让用户敢于让 GPT 自动合并，因为任何一步都能找回。

### 6.7 成本统计

系统应记录：

- worker 模型。
- provider。
- 角色。
- 运行时间。
- 重试次数。
- diff 大小。
- 测试结果。
- merge 结果。
- token usage，如果可得。
- estimated cost，如果配置中有价格表。

成本统计是工程可观测性，不是商业化功能。

## 7. 验收标准

### 7.1 MVP 验收

- 单 worker 能用本地配置模型跑真实任务。
- API key 不进入 Git。
- 新文件能进入 patch。
- UTF-8 输出不导致崩溃。
- 机器审查能识别失败和越界。
- 所有测试通过。

### 7.2 后台化验收

- Agent Farm 能常驻后台。
- Codex 能通过某种 bridge 调用 Agent Farm。
- CLI 仍可作为调试入口。
- 后台能返回 task id、状态和 artifact 路径。

### 7.3 自动合并与回退验收

- GPT supervisor approval 后可自动 merge。
- 自动 merge 前创建 checkpoint。
- 用户可通过 Codex 触发 `go back`。
- 回退后工作区恢复到 merge 前状态。
- rollback record 可审计。
