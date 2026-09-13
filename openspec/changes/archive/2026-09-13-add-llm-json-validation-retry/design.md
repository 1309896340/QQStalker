## Context

现状（见 proposal.md - Why）：

- JSON 输出约定目前只存在于讨论分析的两个阶段：分段议题识别（`discussion_analysis.parse_segment_response`）与议题归并（`parse_merge_response`）。两者已通过 `_validated_request`（`discussion_analysis.py:698`）实现"2 次尝试 + 纠错反馈"重试，JSON 解析失败（`_response_object`）与结构校验失败都会触发重试。
- 但响应文本在 `request_portraits`（`analyze_transcript.py:1227`）返回后立即写入 `.llm-cache/`，早于任何下游校验。非法 JSON 被永久缓存，每次重跑都先命中投毒条目、重试一次才能恢复，既浪费请求又放大错误暴露面。
- 重试的纠错反馈 prompt 与原始 prompt 天然构成不同缓存键，重试本身不会被投毒条目挡住，该机制保持不变。
- 阶段失败的降级产物（`classify_chunk` 中的"未识别片段"候选）已使用语义中立占位说明，但没有回归测试锁定"错误文本不进入后续请求"。

## Goals / Non-Goals

**Goals:**

- 非法 JSON 响应不得在缓存中存活：下游校验判非法时清除对应缓存条目，本次与后续运行均视为未命中。
- JSON 校验重试的尝试总次数有统一上界，并与现有纠错反馈机制一致。
- 以回归测试锁定降级产物不携带错误文本进入后续大模型请求。

**Non-Goals:**

- 不改变各阶段 prompt 内容、报告结构与降级形态（拒答记录、跳过分段、摘录纪要均保持）。
- 不为非 JSON 阶段（成员画像、语录精选为纯文本输出）引入 JSON 校验。
- 不改动缓存键构成与 `--no-cache` 行为。

## Decisions

**D1：采用"校验失败即清除"而非"校验通过后才写入"。**
阶段级校验的权威逻辑（行号区间、覆盖归一化）依赖请求上下文（分段消息），位于 `discussion_analysis` 的解析器中；`request_portraits` 是通用传输层，无法在写入前执行完整校验，预先校验只能做成弱校验，仍会让"JSON 可解析但结构非法"的响应入库。在解析器拒绝响应的确切位置回调清除，覆盖解析失败与结构失败两类，且不需要在传输层复制校验逻辑。
实现：`LlmResponseCache` 暴露受控的 `discard` 入口（复用现有键计算与 `_discard` 机制，`llm_cache.py:50` 已有损坏条目清除先例）；`analyze_transcript` 提供按阶段构造的"按 prompt 清除"回调，经 `analyze_discussion_minutes` 传入 `_validated_request`，在 `parser(response)` 抛出时以当次实际使用的 `request_prompt`（含纠错反馈）调用清除。回调为可选参数，无缓存时为空操作。

**D2：重试上界统一为 3 次尝试（含首次）。**
现状分段/归并为 2 次、纪要压缩为 3 次（`MINUTES_REQUEST_ATTEMPTS`）。JSON 校验失败多为可修复的格式抖动，2 次已能覆盖绝大多数；但用户案例表明存在反复失败的场景。统一为 3 次与纪要阶段惯例对齐，定义为模块常量（如 `VALIDATED_REQUEST_ATTEMPTS = 3`），未来新增 JSON 阶段复用同一常量，避免各阶段上限漂移。

**D3：通用契约落在 `discussion_analysis` 的现有帮助函数上，不新建模块。**
目前仅讨论分析消费 JSON 输出；把 `_validated_request` 的尝试次数常量化并补充文档注释（声明新增 JSON 阶段必须复用该入口），比提前抽象一个跨模块校验框架更符合现有规模。若未来出现第二个 JSON 消费方再考虑抽包。

**D4：降级隔离以回归测试固化，不改运行逻辑。**
`classify_chunk` 的降级候选文案已语义中立；拒答记录只写 `temp/` 不回流请求。补充测试断言：分段失败后，归并阶段的请求输入不含原始错误响应与校验错误文本。

## Risks / Trade-offs

- [清除回调与并发写入竞争] → 键级文件操作天然隔离；`discard` 幂等，条目不存在时静默返回，与现有 `_discard` 行为一致。
- [重试上界提升到 3 次增加最坏情况成本] → 仅在连续校验失败时发生，且第 2、3 次尝试可命中各自缓存键；相比投毒缓存反复重跑的浪费可接受。
- [清除回调遗漏新阶段] → 契约要求新增 JSON 阶段必须复用 `_validated_request`，回调在该入口内部统一触发，不依赖各阶段自行接线。

## Open Questions

（无）
