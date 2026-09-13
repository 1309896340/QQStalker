## 1. 依赖与基础设施

- [ ] 1.1 在 `pyproject.toml` 显式声明 `rich>=13` 并运行 `uv sync`，验证 `uv run python -c "import rich"` 成功且锁文件无意外变更
- [ ] 1.2 新增 `LlmProgressReporter` 协议与两个实现（`RichProgressReporter`、非 TTY 逐行降级实现），`RichProgressReporter` 支持注入 `rich.console.Console`；`begin/update/finish/print` 行为符合 design D2/D5，新增单元测试覆盖：并发多 task 各行独立刷新、动态增删 task、注入 `force_terminal=False` 的 Console 时输出不含回车符或 ANSI 控制字符（`uv run pytest tests/ -k progress`）

## 2. 请求层接入

- [ ] 2.1 修改 `request_portraits`/`stream_chat_completion`/`llm_wait_heartbeat`：进度上报改用协议实例（`begin`/流式增量 `update`/`finally finish`，重试时更新该行重试状态），结果性输出（开始、完成、重试提示、错误）在 reporter 活跃期间经 `reporter.print` 输出；现有无 reporter 调用路径行为不变，相关既有测试通过（`uv run pytest tests/test_analyze_transcript.py`）
- [ ] 2.2 删除 `LiveProgressLine`、`stdout_supports_refresh`、`_enable_windows_virtual_terminal` 及其测试，确认 `uv run pyright` 与 `uv run python -m compileall -q src` 通过

## 3. 阶段挂接

- [ ] 3.1 一体化生成入口创建共享 reporter，以下传参数挂接画像批次、成员补偿、群像速览、语录精选、讨论纪要（`request_discussion` 闭包）各阶段；验证并发场景：小样本运行一体化生成，讨论纪要并发期间多个请求各行同时刷新、完成日志出现在进度区上方且不交错
- [ ] 3.2 流式调试入口挂接同一 reporter；重定向 stdout 到文件运行一次，验证文件内容仅含开始/完成逐行输出且无 `\r`/`\x1b`（`grep -P "\r|\x1b" 输出文件` 为空）

## 4. 回归验证

- [ ] 4.1 全量验证：`uv run pytest`、`uv run pyright`、`uv run python -m compileall -q src` 全部通过；确认重试、超时、截断补偿等既有语义测试未改变
- [ ] 4.2 对照规格验收：逐条核对 `specs/llm-streaming-transport/spec.md` 增量中的 7 个场景（并发多行刷新、动态收缩、串行单行、结果输出不乱序、重定向降级、快速请求无噪音、不泄露内容与密钥）均有实现与测试对应
