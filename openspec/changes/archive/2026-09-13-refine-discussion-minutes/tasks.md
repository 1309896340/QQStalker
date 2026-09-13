## 1. 议题筛选与纪要生成（discussion_analysis.py）

- [x] 1.1 在 `SEGMENT_PROMPT` 中增加实质性判定标准（以寒暄闲聊、表情包斗图、截图交流或无明确主题的重复刷屏为主、缺少可总结观点或信息的区间为无实质内容）与 `substantive` 布尔字段，`parse_segment_response` 校验该字段存在且为布尔值（缺失或非法走既有重试路径），`TopicCandidate` 携带该标记；通过测试验证字段校验、重试与区间覆盖约束不变。
- [x] 1.2 让归并议题按"任一候选实质即实质"在代码中确定性继承实质性（`MERGE_PROMPT` 不新增字段），并让 `analyze_discussion_minutes` 在排序取 top-k 前过滤无实质候选、仅为实质议题撰写纪要；通过测试验证高热度闲聊议题被排除、后续实质议题递补、实质议题不足不凑数、全部无实质时走空报告路径。
- [x] 1.3 将 `MAX_MINUTES_CHARACTERS = 300` 定义为模块常量，重写 `_minutes_instruction`（partial 与 merge 两个变体）为主题式归纳：核心观点、关键分歧与讨论结果成段，合并同类发言、省略寒暄与重复，核心观点与分歧归属发言者，仅在直接证据下写认同/否认，目标 200~300 字且内容不足时如实缩短；删除"按消息时间顺序呈现"强制句；通过更新 `tests/test_analyze_transcript.py` 中纪要提示词断言验证新旧文风要求。
- [x] 1.4 在 `normalize_minutes` 中剥离行内强调标记（`**`/`__`）并增加 300 字上限校验（超限抛 `RuntimeError` 以触发 `_validated_request` 的一次重试），在 `summarize_topic` 中为重试仍超限的情况实现句子边界确定性截断兜底（保留不超过 300 字的最后一个句末标记，超长单句硬切）；通过测试验证超限重试、兜底截断、正常长度直通与短内容不虚构（无下限校验）。
- [x] 1.5 在 `DiscussionReport` 上按成员名单实现纪要文本与"主要参与者"行的姓名加粗包裹（最长匹配优先），并基于纪要中实际出现的姓名按首次出现顺序以黄金角色相生成"成员名→{文字色, 背景色}"映射（文字取同色相深色、背景取同色相高亮度低饱和浅色），随 `chart_payload` 一并注入渲染层；通过测试验证同一成员映射唯一、不同成员颜色互异且色相差异明显、`**姓名**` 包裹正确且不叠加。
- [x] 1.6 将议题配色从 5 色循环复用升级为精选高区分度调色板（色盲友好定性色），议题数超过调色板容量时按索引以黄金角色相确定性生成互不重复的颜色；通过测试验证小数量时使用精选调色板、超容量时颜色互不重复且输出确定性。

## 2. 报告组装与渲染（analyze_transcript.py / render_html_png.py）

- [x] 2.1 将 `to_markdown` 的栏目标题改为 `## 纪要` 并移除 `### 讨论热度` 小节与热度表格（空议题降级文案同步改为"纪要"），`analyze_transcript` 专题失败降级文案同步改为 `## 纪要\n\n纪要暂不可用。`；控制台进度文案保持不变；通过 `tests/test_analyze_transcript.py` 的文档顺序与降级断言验证。
- [x] 2.2 更新 HTML 组装脚本：H2 查找键改为 `纪要`，删除 heatHeading/fallback 与表格回退逻辑，`.discussion-chart` 容器插入 discussion 区块顶部（首个议题 H3 之前），图表初始化失败时隐藏容器，`ready/failed` 状态标记保留；遍历 discussion 区块内 `<strong>` 并按注入的"成员名→{文字色, 背景色}"映射应用姓名着色与浅背景（精确匹配才应用）；通过 HTML 断言验证图表容器位置、无表格、样式映射不被模型文本伪造。
- [x] 2.3 更新 `render_html_png.wait_for_document_resources` 中涉及表格回退的注释并确认 CDN 超时兜底（隐藏 `.discussion-chart`）与状态等待逻辑不变；通过 `tests/test_render_html_png.py` 验证等待条件与失败降级行为保持。

## 3. 回归验证

- [x] 3.1 扩展 `tests/test_analyze_transcript.py`：标题为"纪要"、Markdown 无"讨论热度"表格、报告顺序、高热度闲聊议题排除与递补、纪要 ≤300 字约束、主题式提示词、姓名加粗与颜色映射注入、议题配色互不重复；运行 `uv run python -m unittest tests.test_analyze_transcript` 验证。
- [x] 3.2 扩展 `tests/test_render_html_png.py`：图表成功时无回退表格、失败时无空白图表容器且文字纪要完整、PNG 等待状态标记不变、姓名着色与浅背景仅应用于精确命中的成员名；运行 `uv run python -m unittest tests.test_render_html_png` 验证。
- [x] 3.3 运行 `uv run pyright` 与 `uv run python -m compileall -q src`，并用脱敏短记录做一次端到端生成，人工核对 PNG 中栏目标题、无表格、纪要篇幅与人名高亮效果；提醒归档顺序：先归档 `add-portrait-discussion-minutes`，再归档本变更。
