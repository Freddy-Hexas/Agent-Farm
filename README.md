# Agent Farm

Agent Farm 是一个面向 Codex 的本地 worker orchestrator。它的目标不是重新发明一个 coding agent，而是在 Codex CLI 已有的 agent loop、文件编辑、shell 执行、sandbox 和 git diff 能力之上，补上一层更适合多 worker 协作的调度、隔离、证据收集和审查流程。

一句话概括：

```text
高级 Codex 负责拆任务、审查和合并；
低成本 Codex worker 在隔离 worktree 里完成局部实现；
Agent Farm 负责调度、记录、测试、机器审查和 patch 管理。
```

## 项目目的

当前很多 agent 工作流的问题不在于模型不会写代码，而在于缺少工程边界：

- worker 直接污染主工作区；
- 低价模型改动范围失控；
- 审查依赖 worker 自述，而不是 diff 和测试证据；
- 多 worker 并行时缺少状态机、日志和可回滚产物；
- 成本控制只停留在“换便宜模型”，没有任务拆分和验收制度。

Agent Farm 要解决的是这些工程化问题。它把便宜模型放进 Codex harness 里，让 worker 仍然具备读代码、改代码、跑命令、产出 diff 的能力，同时用独立 git worktree 和机器审查把风险关在可控范围内。

## 项目内容

当前 MVP 包含一个单 worker 工作流：

1. 读取 supervisor 编写的任务规格。
2. 基于指定 base commit 创建独立 git worktree。
3. 使用 `codex exec` 启动一个 Codex worker。
4. worker 在隔离 worktree 中读代码、改代码、跑局部验证。
5. orchestrator 收集 worker 产生的 `git diff`、日志和最终报告。
6. orchestrator 重新运行配置好的测试命令。
7. 机器审查检查 forbidden paths、allowed paths、diff 大小、测试结果、lockfile 变更和删除测试等风险。
8. 只有通过机器审查后，supervisor 才能显式执行 merge。

核心模块：

- `agent_farm/cli.py`：命令行入口。
- `agent_farm/orchestrator.py`：任务状态流、worktree 生命周期、worker 调用和 merge 流程。
- `agent_farm/codex_worker.py`：`codex exec` 参数构造与进程执行。
- `agent_farm/review.py`：机器审查规则。
- `agent_farm/git_ops.py`：git worktree、diff、patch apply 等操作。
- `tests/`：机器审查和配置加载的基础测试。

## 项目意义

Agent Farm 的核心价值是把“多模型协作”变成“多 agent 工程流程”。

它强调的不是让便宜模型裸跑，而是让便宜模型继续运行在 Codex 的工作范式里：

```text
cheap model + Codex CLI + tools + sandbox + worktree + diff review
```

这样可以形成更清晰的分工：

- 高级模型负责理解需求、拆分任务、判断风险和最终审查。
- 便宜模型负责局部、可测试、可回滚的实现工作。
- orchestrator 负责工作区隔离、预算边界、证据收集和机器 gate。

这使得未来的 Codex workflow 可以更接近真实软件团队：有人拆任务，有人实现，有人审查，有测试和日志，有明确的合并权限。

## Quick Start

初始化配置：

```powershell
python -m agent_farm init
```

编写任务规格，例如 `task.md`：

```markdown
# Task

Add rate limiting to the login endpoint.

Allowed scope:
- auth/login code
- auth tests

Acceptance:
- existing auth tests pass
- add a test for repeated failed login attempts
```

启动一个 worker：

```powershell
python -m agent_farm run --task .\task.md --model gpt-5-mini --allow src/auth --allow tests/auth --test-cmd "python -m unittest discover"
```

查看运行结果：

```powershell
python -m agent_farm review --run .\.agent-farm\runs\<task-id>
```

审查通过后应用 patch：

```powershell
python -m agent_farm merge --run .\.agent-farm\runs\<task-id> --yes
```

清理 worker worktree：

```powershell
python -m agent_farm cleanup --run .\.agent-farm\runs\<task-id>
```

## Requirements

- Git with worktree support.
- Codex CLI available as `codex`.
- A git repository with at least one commit. Git cannot create a worktree from an unborn branch.

## Run Artifacts

每次运行会写入一个 `.agent-farm/runs/<task-id>/` 目录：

- `result.json`：任务状态、配置、改动文件、测试结果、机器审查结果。
- `worker-prompt.md`：实际发送给 worker 的任务 prompt。
- `worker-events.jsonl`：Codex JSON event stream。
- `worker-stderr.log`：worker stderr。
- `worker-final.md`：worker 最终报告。
- `patch.diff`：worker worktree 中产生的 binary-capable git diff。
- `tests/*.log`：orchestrator 重新运行测试命令后的日志。

## 安全边界

当前默认策略偏保守：

- worker 在独立 worktree 中执行，不直接修改 supervisor 工作区；
- worker 只产出 patch，不允许自动 merge；
- merge 需要显式 `--yes`；
- 可配置 allowed paths 和 forbidden paths；
- 默认禁止 `.env`、secret、credential、token、CI workflow 等敏感路径；
- 默认不接受 lockfile 变更，除非配置允许；
- 测试失败或 worker 失败会导致机器审查失败。

## 未来 Update 计划

### Phase 1: 单 Worker MVP 完善

- 增加更完整的 task spec schema。
- 增加 worker 运行时间、工具调用次数、diff 行数和文件数的硬限制。
- 增加对 Codex JSON event stream 的结构化解析。
- 增加失败后的 revision prompt 生成。
- 增加更多内置机器审查规则，例如禁止删除测试、禁止改部署配置、检测大规模重命名。

### Phase 2: 半自动 Worker Farm

- 支持多个 worker 并行运行。
- 支持 explorer worker、implementation worker、reviewer worker 三种角色。
- 支持任务队列和状态机持久化。
- 支持模型分级策略：cheap、mid、senior。
- 支持每个任务独立预算、超时、重试次数和上下文裁剪。
- 自动生成 supervisor review report。

### Phase 3: 可替换 Model Endpoint

- 抽象 model adapter / endpoint layer。
- 支持 Codex worker 使用不同 provider 或本地模型。
- 支持 OpenAI-compatible / Responses-compatible gateway。
- 支持按任务类型选择模型。
- 记录每次 worker 的模型、耗时、成本估算和成功率。

### Phase 4: 受控自动合并

- 对低风险任务启用自动合并策略。
- 支持风险分级和 merge policy。
- 支持自动生成 PR 描述。
- 支持和 GitHub Actions / CI 状态联动。
- 对权限、支付、数据库、部署、安全逻辑等高风险区域保持人工确认。

### Phase 5: Supervisor Workflow

- 增加 supervisor-facing CLI 或 TUI。
- 支持从自然语言需求自动生成 task spec。
- 支持对多个 worker diff 做比较和选择。
- 支持回滚、abandon、rerun、request revision 等完整生命周期。
- 支持长期指标统计：一次通过率、返工次数、平均成本、测试失败率。

## 当前状态

这是一个早期 MVP，重点是跑通最小可用闭环：

```text
task spec -> isolated worktree -> Codex worker -> diff/tests/logs -> machine review -> explicit merge
```

后续的重点不是让 worker 更“自由”，而是让 worker 更可控、更可验证、更便宜、更容易被高级 Codex 或人类 reviewer 接管。
