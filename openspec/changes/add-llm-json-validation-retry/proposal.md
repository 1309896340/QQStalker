## Why

讨论分段识别曾出现模型返回非法 JSON（`title` 字段缺引号）的场景：非法响应在通过结构校验前就被写入 `.llm-cache/` 持久缓存，后续重跑会命中同一条投毒缓存并重复失败；同时阶段失败的错误信息还可能以候选议题的形式进入下一步骤的请求（归并、纪要），造成连锁报错。需要把"JSON 响应必须校验、不通过必须重试、非法结果不得传播"固化为通用规范。

## What Changes

- 建立通用的大模型 JSON 响应校验契约：所有以 JSON 为输出约定的请求（分段议题识别、议题归并，及后续新增 JSON 阶段）必须在本地完成 JSON 解析与结构校验（根对象、必填字段、类型、区间合法性）。
- 校验不通过时携带校验错误作为纠错反馈自动重试，重试总次数有上界；全部重试失败时保留现有拒答记录（`temp/*_illegal.md`）与"跳过分段"降级行为，且降级产物不得把原始错误响应文本注入后续大模型请求。
- 修复缓存投毒：未通过 JSON 校验的响应 MUST NOT 写入 `.llm-cache/`；已写入但在下游校验中被判非法的缓存条目必须被清除并在本次与后续运行中视为未命中。
- 重试产生的纠错反馈 prompt 与原始 prompt 使用不同缓存键的现有行为保持不变，确保重试不会被同一投毒条目挡住。

## Capabilities

### New Capabilities

- `llm-json-response-validation`：通用的大模型 JSON 响应校验与重试契约——解析、结构校验、纠错反馈重试的上界、最终失败的拒答记录与降级隔离要求。

### Modified Capabilities

- `llm-response-cache`：写入条件由"请求成功即写"改为"响应通过下游 JSON 校验方可写入"，并新增校验失败条目的清除（视为未命中）要求。
- `portrait-discussion-minutes`：分段识别与议题归并的响应校验重试由固定"重试一次"改为引用通用 JSON 校验契约的带上界重试；明确跳过分段的降级候选不得携带原始错误响应进入后续请求。

## Impact

- `src/qqstalker_cli/discussion_analysis.py`：`_validated_request`、`_response_object`、`write_refusal_trace`、`classify_chunk`（重试上界参数化、通用化校验入口）。
- `src/qqstalker_cli/analyze_transcript.py`：`request_portraits` 缓存写入点（line ~1227）需要与下游校验联动（校验前不写、失败清除）。
- `src/qqstalker_cli/llm_cache.py`：新增按缓存键清除条目的能力（`_discard` 已有内部机制，需暴露受控入口）。
- `tests/test_analyze_transcript.py`：缓存写入/清除与校验重试的用例。
- 不改变各阶段对外输出的报告结构；不改变纠错反馈 prompt 的措辞约定。
