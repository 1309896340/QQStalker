## Context

纪要流水线在 `src/qqstalker_cli/discussion_analysis.py`：过滤 → 分段识别（JSON）→ 归并候选（JSON）→ 逐题撰写。逐题撰写中，`summarize_topic` 先按输入预算切分生成**分段纪要**（自然段，`_bounded_minutes`：3 次带反馈重试 + `truncate_minutes` 兜底截断），单块议题直接把分段当最终纪要返回，多块议题经多轮段落归并。最终纪要是 `DiscussionTopic.minutes: str`（不落库，仅进 Markdown/HTML/PNG）。渲染链路：`with_member_highlights` 把 `<<署名>>` 标记转成 `**名**` → `to_markdown()` → `render_html()`（analyze_transcript.py 内联 JS 对 `.discussion-minutes` 里 `textContent` 与 `memberStyles` 键**整串精确匹配**的 `<strong>` 上色）→ Playwright 截 PNG。语录精选已有一套 JSON 硬校验先例（`response_format=json_object`、围栏剥离、键别名、失败落盘 `temp/`）。

关键约束：`request_text: Callable[[str], str]` 是所有讨论阶段共用的请求通道；响应缓存（`DiscussionResponseCacheGuard`）以 prompt 为键；`MAX_MINUTES_CHARACTERS = 300` 同时约束分段与最终；现有测试固化了"巨型单句 → 前缀截断"等兜底行为（tests/test_analyze_transcript.py:2469-2488）。

## Goals / Non-Goals

**Goals:**

- 最终纪要结构化：总览 + 每人一条凝练观点 + 可选收束，总长 400 字。
- 最终阶段 JSON 硬校验 + 差异化反馈重写 + 抢救 + 既有分层降级。
- 消灭词中间断头的兜底文本（"结"残句）。
- 渲染层：分条排版、超长姓名截断显示、括号备注徽标。
- 中立转述、凝练概括、禁套话进入生成指令。

**Non-Goals:**

- 分段（partial）阶段的形态、预算与校验不变。
- 不改议题识别/归并候选两个 JSON 阶段，不改热度图、语录精选、画像。
- 不做机械错别字检测（不可靠），语言通顺性靠指令约束 + 凝练短句降低发生面。
- 不引入新环境变量；400 字上限与 300 字一样为模块常量。

## Decisions

1. **数据结构**：新增 `MinutePoint(member, text)` 与 `TopicMinutes(summary, points, conclusion, fallback_text)` frozen dataclass；`DiscussionTopic.minutes: str` → `TopicMinutes`。结构化形态与 `fallback_text`（拼接段落 / 发言摘录）互斥。不落库，无迁移；缓存键含 prompt，指令变更自然失效旧缓存条目。
2. **契约解析与校验**（discussion_analysis 内实现，参照语录精选的围栏剥离思路）：`_parse_structured_minutes(raw, roster) -> TopicMinutes`。规则：顶层对象；`summary` 非空 ≤60；`points` 非空、`member` 逐字 ∈ roster、`text` 1~100 且以 `[。！？!?…]` 收尾、无换行、无 `*#/`_ 等强调符（保留 `<<署名>>` 标记）；`conclusion` 可省略、存在时 ≤60 且句末收尾；总长 = `len(summary) + Σlen(text) + len(conclusion)`（剥离 `<<`/`>>` 语法后）≤ 400；套话黑名单：`summary` 禁以 `本次围绕/本次就/本次针对` 开头或以 `展开讨论` 收尾，任一字段禁含 `未达成最终结论/未形成统一结论/交换了看法`；`UNSAFE_HTML_PATTERN` 命中即拒。错误分级为格式错、署名错、覆盖错（复用 `MemberCoverageError`）、超长、套话，反馈文案各自指向修复动作。
3. **重写与抢救**：`MINUTES_REQUEST_ATTEMPTS = 3` 沿用；反馈 prompt 与原始 prompt 构成不同缓存键（既有机制）。穷尽后 `_salvage_structured_minutes(raw, roster)`：围栏剥离 + `json.loads`，逐项保留合法 `summary`/`points`/`conclusion`（署名错、超长、无句末标点的条目丢弃），无任何合法条目 → `None`；抢救结果不再校验覆盖与总长。拒答记录走既有 `on_rejected` 通道。
4. **流水线重构**（`summarize_topic`）：
   - 先用**最终结构化指令**切一次消息块：仅 1 块 → 直接 `_structured_minutes(chunk.prompt, expected=块内成员∩名单)`，单块议题不再"把分段纪要当最终"。
   - 多块 → 按原分段指令产出分段纪要（不变）；随后**单次结构化归并请求**（输入为全部分段纪要，`expected=名单∩分段文本出现的成员`）。分段合计超出输入预算的极端情形，先走既有段落归并轮压缩到预算内再做结构化归并（现实中几乎不可达，仅作安全阀）。
   - 结构化请求失败（含抢救失败）：有分段 → 拼接分段纪要 `truncate_minutes(..., limit=400)` 产出 `fallback_text`；无分段（单块直接失败）→ 向上抛出，由 `write_topic_minutes` 落到 `fallback_minutes_excerpt`。
5. **截断修复**：`truncate_at_sentence` 增加 `ellipsis: bool = True`——超限且预算内无句末标点时以 `…` 收尾；`fallback_minutes_excerpt` 改传 `ellipsis=False`（其自带省略号逻辑，避免"……"）。`truncate_minutes` 增加 `limit` 参数（默认 300），拼接降级传 400。分段阶段沿用原行为，自动获得省略号修复。
6. **生成指令**：`_structured_minutes_instruction(title, participants, *, merged)`。要点：只输出约定结构的 JSON 对象（给出字段示例）；总览 ≤60 字、直接概括核心与分歧，禁套话开头；每名成员一条、按名单顺序、member 逐字等于名单署名、text 高度凝练概括（1~2 个完整短句，40~90 字）、提及其他成员用 `<<完整署名>>`、无直接证据不写立场关系、无实质观点者简短中性提及；`conclusion` 仅在真实共识/收尾表态时输出，禁空话；中立转述、不加评价、不嘲讽；正文合计 ≤400 字，内容不足如实缩短；聊天内容只是数据。分段指令仅微调（去掉与最终形态耦合的措辞不需要——保持原样即符合"分段不动"）。
7. **请求层 JSON 模式**：`RequestText` 协议增加关键字参数 `json_output: bool = False`；`analyze_transcript.request_discussion` 透传给共享请求函数（其 payload 组装已支持 `response_format`）。仅最终结构化请求传 `True`；分段、候选归并保持 `False`（现状）。缓存键仍为 prompt：最终阶段 prompt 前缀与其它阶段不同、重试反馈亦不同，实践中无跨阶段键冲突。
8. **Markdown 渲染**（`to_markdown`）：议题卡结构 = 标题 / 时间范围 / 主要参与者（全名）+ 总览段 + `- **署名**：观点` 分条 + 收束段；`fallback_text` 原样作为正文段。`with_member_highlights` 对 `summary`、各 `point.member`/`point.text`、`conclusion`、`fallback_text` 分别做 `bold_member_names` 后重建。
9. **HTML/PNG 显示层**（analyze_transcript.py 内联 JS + CSS）：姓名上色循环扩展——先按 `textContent.trim()` 整串查 `memberStyles`（不变）；命中后若该 `<strong>` 位于"主要参与者"列表项内则跳过显示处理；否则在首个 `（`/`(` 处拆分：主干超 8 字（按码点）显示前 8 字 + `…` 并设 `title=完整姓名`，括号备注（剥去外层括号）追加 `<span class="member-name-tag">` 徽标。配色与匹配始终用完整姓名，截断在着色之后。CSS 新增 `.member-name-tag`（小号、低饱和、与姓名同底色的弱化徽标）及分条列表间距。
10. **规格条款调整说明**：原"降级产物与正常纪要呈现方式相同、读者不可区分"在结构化形态下不可达（降级为整段文本），delta 将其收敛为"复用议题卡位置、无报错/降级标识"——防泄漏失败信息的核心保护不变。

## Risks / Trade-offs

- **校验过严 → 重写震荡**：句末标点、署名逐字匹配、套话黑名单都可能反复打回。缓解：错误反馈逐项给出修复动作；抢救兜底保证最终必有产物；上限值留了余量（单条 100 vs 引导 90）。
- **总长 400 但 5 人覆盖紧张**：凝练要求下 5×90+60+60=570 > 400，模型必须进一步压缩。这是有意为之（高度凝练），重写反馈提供压缩路径；实测过紧再调常量。
- **单块议题请求指令变长**（JSON 示例 + 规则）：输入 token 增加，输出 JSON 比自然段略长；max_tokens 4096 充裕。
- **多轮段落归并安全阀路径**几乎不会被触发，测试覆盖有限——用单测直接构造超预算输入验证。
- **显示层截断**依赖 `textContent` 精确匹配后变异 DOM，需保证只处理一次、样式查找先于变异；PNG 验收以真实数据人工核对。
