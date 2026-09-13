## Why

讨论纪要阶段允许多个大模型请求并发（`LLM_DISCUSSION_CONCURRENCY`，默认 4），但每个请求各自创建一个单行 `LiveProgressLine`，都往同一行 stdout 写 `\r\x1b[2K…`，互相覆盖；"响应完成"等普通 `print` 再插进来，控制台输出交错混乱，无法分辨哪个并发请求处于什么状态。需要把并发请求的进度以多行形式同时显示、同时更新。

## What Changes

- 引入 `rich.progress.Progress` 作为统一的进度呈现层：一个进行中的大模型请求对应一个进度 task，多行同时显示、各自独立更新，支持并发期间动态增删 task。
- 请求进度数据（流式已接收字符数、已等待时长、重试状态）由 `request_portraits` 上报给共享的 Progress，替代每请求自建的 `LiveProgressLine`。
- 结果性输出（阶段开始/完成、错误、重试提示）改经 Progress 的控制台打印（`progress.console.print`），由 rich 保证插入到进度区上方，不再与进度行交错。
- 画像批次、成员补偿、语录精选、群像速览、讨论纪要等所有大模型请求阶段统一挂接到同一 Progress 体系，串行阶段与并发阶段行为一致。
- 非交互终端（stdout 重定向到文件/管道）自动降级：不输出 ANSI/回车符，仅在请求开始与完成时逐行输出，与现有重定向行为兼容。
- 在 `pyproject.toml` 中显式声明 `rich` 依赖（当前为传递依赖，版本 15.0.0 已在锁文件中）。
- 流式调试入口（独立 debug 命令）沿用同一进度呈现层，保持行为一致。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `llm-streaming-transport`: "实时生成进度"要求从"单行原位刷新"扩展为"多行并发进度显示"——并发请求各自占一行同时刷新；新增终端降级与结果输出不打乱进度区的场景要求。

## Impact

- **代码**：`src/qqstalker_cli/analyze_transcript.py`（`LiveProgressLine` 替换/改造、`request_portraits` 进度上报参数、各阶段挂接）、`src/qqstalker_cli/discussion_analysis.py`（并发 worker 透传进度句柄）、流式调试入口模块。
- **依赖**：`pyproject.toml` 新增 `rich`（锁文件已有，预计 `uv sync` 后无新下载）。
- **测试**：`tests/test_analyze_transcript.py` 等涉及进度输出的用例需适配；新增并发进度行渲染与降级行为的测试。
- **不受影响**：请求重试/超时/错误分类语义、报告内容与 Markdown 结构、响应校验规则均不变；进度输出仍不得包含生成内容或密钥。
