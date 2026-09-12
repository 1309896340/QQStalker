## Why

画像分析的每次大模型请求都是非流式长请求：模型生成 2–5 分钟期间连接上没有任何数据流动，网关/中间层按空闲连接将其掐断（实测报错 `Server disconnected without sending a response.`），且每次重试发出的仍是同样的长请求，4 次尝试大概率以同样方式失败；一旦失败，已生成的内容全部丢失。改为流式（SSE）后 chunk 自首 token 起持续到达，既从根上消除这类空闲超时失败，也提供真实的生成进度。

## What Changes

- 将 `request_portraits` 从一次性 `client.post` + `response.json()` 改为 `client.stream` + SSE 增量解析（`data:` 行、`[DONE]` 结束标记、`choices[0].delta` 增量累积、从最后一个 chunk 读取 `finish_reason`）。
- 用真实生成进度（已接收字符数）替换现有的 15 秒等待心跳日志；阶段标签、请求/完成耗时日志保持不变。
- 中途断流时保留已接收的部分内容：进入日志诊断，并尽量配合现有 `finish_reason == "length"` 的按成员补偿逻辑，而不是全部丢弃后盲目重试。
- 保留现有指数退避重试与错误分类；新增非流式回退开关（默认开启流式），用于不支持 SSE 的端点（如火山 Ark Agent Plan）。
- 新增独立调试入口，可绕过完整画像流程单独验证流式请求模块：读取消息记录样本、走一遍真实流式请求并输出增量进度与结果摘要，默认调试样本为 `exports/20260911194123_消息记录.md`（该文件被 Git 忽略，仅本地使用）。
- 重写受影响的单元测试（现 mock `client.post` 返回整响应的方式改为 mock 流式响应）。

## Capabilities

### New Capabilities

- `llm-streaming-transport`: 大模型流式传输能力——SSE 请求与增量解析、实时生成进度、断流部分内容保留、流式下的重试与超时语义、非流式回退开关，以及流式模块的独立调试入口。

### Modified Capabilities

（无：`contextual-member-portrait-analysis` 的批次划分、上下文构建、成员补偿与配置边界等需求均不因传输方式改变而修改。）

## Impact

- `src/qqstalker_cli/analyze_transcript.py`：`request_portraits` 传输层重写、心跳逻辑替换为增量进度、进度辅助函数调整。
- `src/qqstalker_cli/generate_portrait.py`：无需行为改动（一体化入口复用同一请求函数，自动获得流式）。
- 新增流式调试模块（`src/qqstalker_cli/` 下），以及 `.env.example` 中的流式开关说明。
- `tests/test_analyze_transcript.py`：`LlmErrorTests` 与进度相关测试改为流式响应 mock，新增 SSE 解析、断流保留、回退开关用例。
- 不影响数据库导入、导出、渲染与实时同步模块。
