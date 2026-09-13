## Why

`analyze_transcript` 的群员画像阶段目前逐批串行请求大模型（`analyze_all_members` 中的批处理循环）：批次数随群员数量线性增长，每批耗时由最慢的单次请求决定，整体画像生成时间随批次数叠加。讨论纪要阶段已经通过 `ThreadPoolExecutor` 并发执行相互独立的专题请求，且 `LlmProgressReporter`（`add-concurrent-progress-display` 变更）已具备多请求并发进度显示能力，画像批次可以复用同一套机制显著缩短总耗时。

## What Changes

- 将 `analyze_all_members` 中逐批串行的画像请求改为线程池并发执行：每个批次的"构建上下文 → 请求画像（含批内遗漏成员补偿）→ 规范化"作为独立任务提交，批间并发、批内补偿请求保持串行。
- 新增环境变量 `LLM_PORTRAIT_CONCURRENCY` 控制画像批次的并发数，默认 2（画像批次单请求体积大、且批内补偿可能叠加请求，取比 `LLM_DISCUSSION_CONCURRENCY` 更保守的默认值）；未设置或值无效时按现有环境变量校验规则处理。
- 并发执行 MUST NOT 改变最终报告内容、批次划分、请求预算（`LLM_MAX_INPUT_CHARACTERS`、`LLM_MAX_TOKENS`）、截断判定与遗漏成员补偿规则；最终画像仍按批次原始顺序拼接。
- `skipped_members` 的记录改为线程安全（按任务返回汇总或加锁），批次级日志统一经由 `reporter.print` 通道输出，避免并发交错。
- 进度显示复用 `LlmProgressReporter`：多个画像批次并发时各自占用一行独立刷新，重定向输出时回退为逐行输出（依赖 `add-concurrent-progress-display` 变更已交付的并发进度能力）。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `contextual-member-portrait-analysis`: 新增画像批次并发执行需求——批次相互独立时 SHALL 支持并发请求并受 `LLM_PORTRAIT_CONCURRENCY` 约束，并发 MUST NOT 改变报告内容、请求预算、补偿行为与最终画像顺序。

## Impact

- **代码**：`src/qqstalker_cli/analyze_transcript.py`（`analyze_all_members` 批处理循环、`analyze_member_batch` 的 `skipped_members` 记录方式、环境变量校验入口）；可能将 `discussion_analysis._run_items` 的保序并发模式提取复用或就地实现。
- **配置**：`.env.example` 增加 `LLM_PORTRAIT_CONCURRENCY` 说明。
- **测试**：`tests/test_analyze_transcript.py` 增加并发执行、顺序保持、配置校验用例。
- **依赖**：构建于进行中的 `add-concurrent-progress-display` 变更之上（其已实现 `LlmProgressReporter` 并发进度显示）；讨论纪要阶段行为不受影响，两个阶段的并发配置相互独立、不会同时生效。
