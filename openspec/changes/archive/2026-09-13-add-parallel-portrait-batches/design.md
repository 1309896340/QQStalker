## Context

`analyze_all_members`（`src/qqstalker_cli/analyze_transcript.py`）当前以串行 `for` 循环逐批执行画像分析：构建上下文 → `analyze_member_batch`（单次请求 + 批内遗漏成员逐个补偿）→ `normalize_member_portraits`。讨论纪要阶段已有成熟的并发模式：`discussion_analysis._run_items` 用 `ThreadPoolExecutor` + `executor.map` 实现"并发执行、保序返回"，`analyze_all_members` 内的 `request_discussion` 闭包展示了如何把带锁计数、`LlmProgressReporter` 与 `request_portraits` 组合成线程安全的请求函数。`add-concurrent-progress-display` 变更（进行中，实现已在工作区）交付的 `LlmProgressReporter` 本身就是为多请求并发进度显示设计的，`request_portraits`、trace 落盘（`_TRACE_ALLOCATION_LOCK`）均已线程安全。

约束：并发执行不得改变报告内容、批次划分、请求预算与补偿规则（见 specs 增量）；本变更实现时假定 `add-concurrent-progress-display` 已合入。

## Goals / Non-Goals

**Goals:**

- 画像批次批间并发，批次数较多时显著缩短总耗时。
- 复用现有线程池模式与进度显示，不引入新依赖（asyncio 等）。
- 并发度可配置、可回退到 1（行为与现状等价）。

**Non-Goals:**

- 不改变批次划分算法（`batch_members`）、提示词构建或上下文预算。
- 不把批内遗漏成员的补偿请求并发化。
- 不为讨论纪要与画像阶段建立共享并发预算（两阶段串行执行，不会同时发起请求）。
- 不处理语录精选、群像速览的并发（单请求，无收益）。

## Decisions

1. **线程池 + 保序收集，而非 asyncio**。请求是 `httpx` 同步阻塞调用，线程池与讨论纪要的 `_run_items` 模式一致，改动面最小。备选的 asyncio 方案需要把 `request_portraits` 全链路改造成异步，波及所有调用方，不值得。

2. **复用或提取 `_run_items` 的保序并发原语**。优先将 `discussion_analysis._run_items` 提取为共享工具（如 `src/qqstalker_cli/concurrency.py`）供两处复用；若提取会造成不必要的接口扰动，则在 `analyze_transcript.py` 内就地用 `ThreadPoolExecutor` + `executor.map` 实现同等语义（保序、`min(max_workers, len(items))`、`maximum_workers <= 1` 时退化为顺序执行）。实现时二选一，以测试覆盖为准。

3. **worker 粒度：整批一个任务**。每个批次的"构建上下文 → `analyze_member_batch` → `normalize_member_portraits`"作为一个任务提交，返回该批次的规范化画像文本与该批次跳过的成员名单；`skipped_members` 由主线程汇总，消除共享可变列表。`build_member_contexts` 是只读输入（`chronological_messages`）上的正则计算，放在 worker 线程中无共享写状态。备选的"主线程先建全部上下文再并发请求"会抬高峰值内存且拉长首个请求前的等待，不采用。

4. **批内补偿保持串行**。`analyze_member_batch` 内部对缺失成员的逐个补救循环不变；批间并发已是主要提速来源，批内再并发会使请求尖峰不可控（并发批次 × 批内补偿叠加），且补偿请求依赖批内已有的缺失判定结果。

5. **配置与校验对齐现有模式**。新增 `positive_integer_setting("LLM_PORTRAIT_CONCURRENCY", 2)`，默认 2：画像批次单请求体积大、补偿可能叠加请求，取比 `LLM_DISCUSSION_CONCURRENCY`（默认 4）保守的值；设为 1 时行为与现有串行路径等价。校验失败在 `main()` 加载环境后立即终止，报错格式与 `LLM_DISCUSSION_CONCURRENCY` 一致。

6. **日志与进度通道统一**。批处理循环中现有的裸 `print`（批次标题、本批次群员、上下文构建统计）改经 `reporter.print` 输出；批次阶段标签沿用 `画像批次 {index}/{total}` 格式，使并发进度区中各行可辨识。`LLM_PORTRAIT_CONCURRENCY > 1` 时向用户提示已启用的并发数（对齐讨论纪要阶段的提示风格）。

## Risks / Trade-offs

- [服务端限流：并发批次 + 各自重试放大 429 概率] → 默认并发数取 2；既有指数退避与可重试分类兜底；用户可通过 `LLM_PORTRAIT_CONCURRENCY=1` 回退串行。
- [并发下线程内 `time.sleep` 退避与其他批次争抢线程池槽位] → 线程池大小等于并发数，单个批次的退避等待最多占用自身槽位，不阻塞已完成提交的其他批次。
- [峰值内存：多批次上下文同时驻留] → 上下文单批受 `LLM_MAX_CONTEXT_CHARACTERS_PER_MEMBER × 成员数` 与 `LLM_MAX_INPUT_CHARACTERS` 约束，默认配置下每批不超过约 2.4 万字符量级，风险可忽略。
- [提取 `_run_items` 引入跨模块重构] → 若共享工具与讨论纪要现有测试冲突，退回方案 2 的就地实现，行为契约不变。

## Migration Plan

纯命令行工具内部行为变更，无数据迁移。回滚方式：设置 `LLM_PORTRAIT_CONCURRENCY=1`（等价于原串行行为）或还原代码。

## Open Questions

（无）
