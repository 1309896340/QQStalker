## 1. 缓存清除入口

- [x] 1.1 在 `src/qqstalker_cli/llm_cache.py` 为 `LlmResponseCache` 增加公开的 `discard` 方法：复用现有缓存键计算与 `_discard` 机制，按（阶段、模型、参数、prompt）定位条目并删除，条目不存在时静默返回、幂等。验证：新增单测覆盖"存入后 discard、再 lookup 未命中"与"discard 不存在的键不报错"（`tests/test_analyze_transcript.py` 缓存用例区）。
- [x] 1.2 在 `src/qqstalker_cli/analyze_transcript.py` 提供按阶段构造"按 prompt 清除缓存"回调的帮助函数（无缓存安装时返回空操作）。验证：`uv run pyright` 通过，回调在未安装缓存时调用无异常。

## 2. 校验重试与清除联动

- [x] 2.1 在 `discussion_analysis.py` 将 `_validated_request` 的尝试次数常量化（`VALIDATED_REQUEST_ATTEMPTS = 3`，含首次），并补充文档注释声明新增 JSON 输出阶段必须复用该入口。验证：新增/调整单测断言连续非法响应在第 3 次尝试后停止并抛出 `ValidatedResponseError`。
- [x] 2.2 为 `_validated_request` 增加可选的响应拒绝回调参数，在 `parser(response)` 抛出异常时以当次实际使用的 `request_prompt`（含纠错反馈）调用；`analyze_discussion_minutes` 透传该回调。验证：单测模拟 parser 首次拒绝、断言回调收到的是原始 prompt 而非纠错反馈 prompt；第二次拒绝时断言收到的是反馈 prompt。
- [x] 2.3 在 `analyze_transcript.py` 的讨论分析调用链（`request_discussion` → `analyze_discussion_minutes`）接通清除回调，使校验失败的响应在缓存中被清除。验证：单测安装缓存后模拟非法响应，断言 `lookup` 该 prompt 在校验失败后未命中，且重试通过后的合法响应可正常命中缓存。

## 3. 降级隔离回归锁定

- [x] 3.1 补充回归测试：分段请求经全部重试仍未通过 JSON 校验后，断言归并阶段收到的请求输入包含"未识别片段"语义中立占位说明，且不包含模型原始响应文本与校验错误文本。验证：测试通过。
- [x] 3.2 补充回归测试：非法 JSON（如 `title` 字段缺引号的响应）触发纠错反馈重试且重试成功时，最终议题结果正确、`temp/*_illegal.md` 未生成。验证：测试通过。

## 4. 收尾验证

- [x] 4.1 运行 `uv run pyright` 与 `uv run python -m compileall -q src`，均无错误。
- [x] 4.2 运行全量测试（`uv run pytest tests/` 或现有等价方式）确认无回归，重点核查缓存与校验重试相关用例。
