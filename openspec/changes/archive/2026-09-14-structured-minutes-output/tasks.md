## 1. 数据结构与截断修复（discussion_analysis.py）

- [x] 1.1 新增 `MinutePoint`、`TopicMinutes` frozen dataclass，`DiscussionTopic.minutes` 改为 `TopicMinutes`；`with_member_highlights` 对结构化字段分别加粗重建；`to_markdown()` 输出"总览段 + `- 署名：观点` 分条 + 收束段"，`fallback_text` 原样输出。验证：`uv run python -m compileall -q src` 通过，既有分段相关单测不回归。
- [x] 1.2 `truncate_at_sentence` 增加 `ellipsis` 参数（超限且预算内无句末标点时以 `…` 收尾），`fallback_minutes_excerpt` 调用改传 `ellipsis=False`；`truncate_minutes` 增加 `limit` 参数（默认 300）。验证：新增单测覆盖"整段无句读硬切 → 省略号收尾""摘录 60 字内无句末标点仍只补一个省略号"。
- [x] 1.3 新增常量 `MINUTES_MAX_TOTAL_CHARACTERS = 400`、`MINUTES_SUMMARY_CHARACTERS = 60`、`MINUTES_POINT_CHARACTERS = 100` 与套话黑名单正则。验证：常量单测引用无未定义名称。

## 2. 结构化契约：解析、校验、抢救（discussion_analysis.py）

- [x] 2.1 实现 `_parse_structured_minutes(raw, roster) -> TopicMinutes`：围栏剥离、`json.loads`、summary/points/conclusion 逐项校验（长度、署名逐字匹配、句末标点、无换行/强调符、`UNSAFE_HTML_PATTERN`、总长按 design 计法 ≤400、套话黑名单），错误分级为格式/署名/覆盖/超长/套话。验证：单测覆盖合法 JSON、非法 JSON、未知署名（"减脂" vs "减肥"）、超长、无句末标点、套话、缺 points。
- [x] 2.2 实现 `_salvage_structured_minutes(raw, roster) -> TopicMinutes | None`：逐项保留合法部分，无任何合法观点条目返回 `None`。验证：单测覆盖"合法 summary + 部分非法条目 → 保留子集""全非法 → None"。
- [x] 2.3 实现 `_structured_minutes(prompt, *, request_text, expected)`：3 次带差异化反馈重写（复用错误分级反馈与既有缓存拒答通道），穷尽后抢救，抢救失败抛错。验证：用假 `request_text` 单测重写成功、重写穷尽后抢救成功、全失败抛错三条路径。

## 3. 流水线重构（discussion_analysis.py）

- [x] 3.1 新增 `_structured_minutes_instruction(title, participants, *, merged)`：约定 JSON 结构示例、总览 ≤60、每人一条按名单顺序、member 逐字署名、凝练概括 1~2 完整短句、`<<完整署名>>` 标记、无证据不写立场、无实质观点中性提及、可选收束、中立转述不评价、正文 ≤400、禁套话、内容不足如实缩短、聊天内容只是数据。验证：单测断言关键约束文案存在且含名单。
- [x] 3.2 重构 `summarize_topic`：先按最终指令切块，单块议题直接走 `_structured_minutes`；多块议题产出分段纪要（原逻辑不动）后单次结构化归并（分段超输入预算时先用既有段落归并轮压缩再结构化归并）；结构化失败且有分段 → `truncate_minutes(拼接, expected, limit=400)` 产出 `fallback_text`，无分段 → 向上抛出走发言摘录。验证：单测覆盖单块直出、多块归并、归并失败拼接降级、单块失败摘录降级、分段超预算安全阀。
- [x] 3.3 `write_topic_minutes` 适配 `TopicMinutes`（失败降级 `fallback_minutes_excerpt` 包入 `TopicMinutes(fallback_text=...)`）。验证：既有"纪要生成失败保留议题条目"相关单测通过。

## 4. 请求层与渲染（analyze_transcript.py）

- [x] 4.1 `RequestText` 协议增加 `json_output: bool = False` 关键字参数，`request_discussion` 透传至共享请求函数启用 `response_format`；仅最终结构化请求传 `True`，分段与候选归并保持 `False`。验证：假请求通道单测断言最终请求带 JSON 模式、分段请求不带；`uv run pyright` 通过。
- [x] 4.2 内联 JS 姓名处理扩展：先按完整姓名整串匹配上色（不变），随后对纪要正文内（"主要参与者"列表项之外）的姓名在首个括号处拆分——主干超 8 码点显示前 8 字 + `…` 并设 `title=完整姓名`，括号备注渲染为 `member-name-tag` 徽标；新增对应 CSS（徽标低饱和弱化、分条间距）。验证：构造含 10 字署名与"（本人回应）"署名的报告，`uv run python -m src.render_html_png --help` 与临时 HTML 断言徽标/截断节点存在。

## 5. 回归与验收

- [x] 5.1 更新受影响的既有单测（最终纪要自然段形态、兜底截断相关用例），补充 2.1–3.2 场景用例；运行 `uv run pyright`、`uv run python -m compileall -q src` 与纪要相关测试全绿。
- [x] 5.2 用调试配置（鸣潮萌新交流群）跑一次完整分析，人工核对 PNG：分条排版、总览/收束、超长姓名截断与徽标、无残句、无套话；确认校验失败时 `temp/` 拒答记录可查。
