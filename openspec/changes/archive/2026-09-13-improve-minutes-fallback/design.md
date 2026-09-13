## Context

`src/qqstalker_cli/discussion_analysis.py` 中议题纪要的生成链路是：`filter_discussion_messages` 先把 `TranscriptMessage` 清洗成有效消息（`discussion_text`），`summarize_topic` 按"分段纪要（每段经 `_bounded_minutes` 校验，300 字内自然段）→ 逐级归并"两阶段产出最终纪要。任何一环抛 `RuntimeError` 时，`write_topic_minutes` 捕获并调用 `fallback_minutes_excerpt`，把失败原因（含被拒响应开头 80 字）嵌进正文头部。f7f6403 引入的大模型响应文件缓存保留了原始响应，可用于误杀取证。

## Goals / Non-Goals

**Goals:**

- 报告正文零报错：降级产物与正常纪要呈现一致，诊断只走 CLI 警告。
- 分层降级：分段级跳过 → 归并失败拼接分段纪要 → 全失败才发言摘录兜底。
- 有效消息清洗修真：裸媒体文件名标记排除、`[回复消息]` 前缀不剥坏。
- 摘录句子边界截断。

**Non-Goals:**

- 不改议题识别、归并提示词与 300 字校验策略本身（误杀排查仅限简单判定 bug）。
- 不改热度图、渲染与高亮逻辑。
- 不调整摘录条数（8）与长度（60 字）、纪要上限（300 字）。

## Decisions

1. **分层降级落在 `summarize_topic` 内部，而非 `write_topic_minutes`**。分段纪要在归并失败时本就存在但被丢弃；在 `summarize_topic` 内捕获分段/归并失败并就地降级，能直接复用局部变量里的已校验分段纪要，函数继续返回 `str`，上层 `write_topic_minutes` 的 try/except 仅保留为"全部分段失败"的最后防线。
   - 备选：把分段纪要提升为返回值向上传递再拼接——需要改函数签名和调用方，收益相同。
   - 细节：分段循环中单段 `_bounded_minutes` 抛错时打印 CLI 警告（含原因）并以 `None` 占位；归并树构建时跳过 `None`；若归并调用最终抛错，拼接当前剩余段落用现有 `truncate_minutes` 截回 300 字；全部段落失败才向上抛错。
2. **`fallback_minutes_excerpt` 去掉 `reason` 参数与头部**，改为固定中性引导"主要发言摘录："；`write_topic_minutes` 的 except 分支继续负责把完整原因 print 到 CLI。签名变更同步更新调用方与测试。
3. **`discussion_text` 增加裸媒体标记模式而非扩 strip 集合**。strip 集合移除 `[`、`]`（及配套的 `【】`、`（）` 收窄）以修复 `[回复消息]` 剥坏问题；裸媒体用独立的正则（`图片:`/`视频:` + 文件名字符序列，整段匹配后为空才排除）识别，复用 PLACEHOLDER_PATTERN 同级的常量。整条内容是纯媒体才排除；文字与媒体混排的消息仍保留文字部分（把媒体标记从文本中剥除后判定）。
   - 备选：把 `图片:xxx.jpg` 归一化成 `[图片]` 再走现有 pattern——多一次改写，且对 LLM 输入的净化效果相同但路径更绕。
4. **句子边界截断抽出公共辅助函数**：摘录 60 字截断与 `truncate_minutes` 同为"截到最后一个句末标点"语义，抽一个小函数供两者复用；60 字内无句末标点时保留原样加省略号（规格允许）。
5. **误杀排查走缓存取证**：实现时先在 LLM 响应缓存目录中定位纪要校验被拒的响应（按错误文案搜索），复现 `_normalize_minutes_text` 的拒绝路径；若是句子数判定/围栏剥离等简单缺陷则修复并补测试，若是策略级问题写入 `docs/proposals/` 并在变更说明中留痕。

## Risks / Trade-offs

- [有效消息集合收紧改变 LLM 输入与热度基数] → 媒体标记本就是无信息 token，输入质量改善；在测试中固定过滤行为，热度差异在报告层面不可感知（讨论度是相对值）。
- [拼接的分段纪要可能主题重复、文风不连贯] → 300 字句末截断兜底视觉一致；这是降级路径，质量下限优先于文笔。
- [归并树中途失败后拼接丢失后续归并机会] → 拼接前已对当前层剩余段落做过 `_text_chunks` 分组，只在模型调用重试穷尽后才拼接，属可接受的降级。
- [strip 集合收窄可能让某些边界内容（行首 `*`、`>` 等）残留] → 逐项核对现有测试断言与真实导出样本，补回归用例。

## Migration Plan

纯代码行为变更，无数据迁移。合并前跑 `uv run pyright`、`uv run python -m compileall -q src` 与 `tests/` 全量用例；用一次真实导出复盘降级路径（可用缓存重放）。回滚即 revert 提交。

## Open Questions

（无）
