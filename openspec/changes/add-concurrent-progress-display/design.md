## Context

进度呈现目前由 `analyze_transcript.py` 中的 `LiveProgressLine`（约 694–719 行）承担：每个 `request_portraits` 调用自建一个实例，用 `\r\x1b[2K` 在单行原位刷新。该机制只支持一行；讨论纪要阶段（`discussion_analysis._run_items`）最多 `LLM_DISCUSSION_CONCURRENCY`（默认 4）个请求并发时，多个实例互相覆盖，普通 `print` 的完成/错误消息还会把刷新中的行顶乱。

约束：进度输出不得包含生成内容与密钥；stdout 重定向时不得出现回车符/ANSI 控制字符；现有重试、超时、错误分类语义不变；Windows 经典控制台需要启用 VT（现由 `os.system("")` 处理）。

## Goals / Non-Goals

**Goals:**

- 并发请求各自占一行、同时显示、独立刷新，支持动态增删行。
- 结果性输出（开始/完成/重试/错误）不打乱进度区。
- 非 TTY 自动降级，保持重定向输出干净。
- 所有 LLM 请求阶段（画像批次、补偿、语录精选、群像速览、讨论纪要、流式调试入口）共用同一套呈现层。

**Non-Goals:**

- 不改变请求内容、重试策略、超时语义、报告内容与 Markdown 结构。
- 不做基于总 token 数的百分比估算（总量未知，仅显示已接收字数/已等待时长）。
- 不引入日志框架重构。

## Decisions

**D1：用 `rich.progress.Progress` 替代手写多行 ANSI 渲染。**

rich 原生支持多 task 并行刷新、动态 add/remove task、内部刷新线程、终端宽度自适应、Windows VT 启用与非 TTY 降级。替代方案（自研 `LiveProgressArea` 多行重绘）需自行处理换行截断、resize、槽位管理与全部输出绕行，维护成本高；已评估并放弃。`rich` 已在 uv.lock 中（15.0.0，传递依赖），只需在 `pyproject.toml` 显式声明，预计无新增下载。

**D2：抽象出进度上报协议，`request_portraits` 依赖协议而非 rich 类型。**

新增轻量协议（如 `LlmProgressReporter`）：`begin(label) -> token`、`update(token, *, received_chars)`、`finish(token, *, status)`。两个实现：

- `RichProgressReporter`：包装共享的 `rich.progress.Progress`，每个请求一个 task；列配置为描述（阶段标签）、spinner、已等待时长、已接收字符数（`completed=received_characters`，total 置 `None`）、重试状态（有重试时更新描述后缀）。
- 非 TTY / 测试用实现：`begin/finish` 逐行 `print`（沿用现有文案），`update` 为空操作。

`request_portraits` 的 `stage_label` 参数保留；`progress_reporter` 改为接受协议实例。替换并删除 `LiveProgressLine`，`_enable_windows_virtual_terminal`/`stdout_supports_refresh` 一并移除（终端探测交给 rich）。非流式路径的 `llm_wait_heartbeat` 同样改用协议实例。

**D3：Progress 实例在一体化生成入口创建，逐层显式传参。**

`analyze_transcript` 的主流程创建一个 `RichProgressReporter`（内部持有一个 `Progress`，`Console` 可注入以便测试），贯穿全部分析阶段，随各阶段函数（`analyze_member_batches`、`analyze_group_overview`、`analyze_featured_quotes`、讨论纪要闭包、流式调试入口）以可选参数下传。讨论纪要的 `request_discussion` 闭包直接把 reporter 传给 `request_portraits`，`discussion_analysis` 无需感知进度（它只调用 `request_text`）。不使用模块级全局单例，避免测试污染与并发运行冲突。

**D4：结果性输出经 `progress.console.print` 打印。**

进度活跃期间，所有阶段消息（"正在请求大模型…"、"大模型响应完成"、重试提示、错误警告）改走 reporter 暴露的 `print`（内部为 `progress.console.print`），rich 会将其渲染在进度区上方；进度区外（如分析前置统计）保持普通 `print`。线程安全由 rich 内部锁保证。

**D5：task 生命周期与重试。**

`request_portraits` 在首次发起请求时 `begin`，重试不删除 task，而是更新该行状态（如"重试 2/3"）；整个函数结束（成功/失败/异常）时在 `finally` 中 `finish`（成功则 `remove_task`，失败保留一行红色状态直至阶段收尾），保证异常路径不残留僵尸行。快速完成的请求（进度间隔内）因从未刷新中间帧，天然无进度噪音，与现有行为一致。

## Risks / Trade-offs

- [rich 刷新线程与手写输出交错] → 规范"进度活跃期间所有输出走 reporter.print"并在 code review/测试中覆盖；测试用注入 `Console(file=StringIO(), force_terminal=True/False)` 验证两种模式。
- [rich 非 TTY 下行为与现有逐行降级文案不完全一致] → 非 TTY 实现不复用 rich（见 D2），逐行文案与现状保持一致，测试锁定"无回车符/ANSI"。
- [显式传参导致多函数签名改动] → 参数可选、默认 `None`，逐阶段小步替换；独立分析入口（`analyze_transcript.py` 单成员/单批次调试路径）同步挂接。
- [PySide6/FastAPI 等已有 GUI 输出与 rich 控制台冲突] → 当前 CLI 入口无 GUI 并存场景；rich Console 仅在 CLI 进程内使用。
- [重定向时 rich 仍可能写入控制序列] → 非 TTY 时 reporter 切换为逐行实现，不创建 `Progress`，从根源避免。

## Migration Plan

1. `pyproject.toml` 显式声明 `rich>=13`，`uv sync`。
2. 实现 `LlmProgressReporter` 协议 + `RichProgressReporter` + 逐行降级实现，替换单元测试。
3. `request_portraits`/`stream_chat_completion`/`llm_wait_heartbeat` 改用协议；删除 `LiveProgressLine`。
4. 各阶段入口创建并下传 reporter；流式调试入口同步。
5. 验证：`uv run pyright`、`uv run python -m compileall -q src`、`uv run pytest`；手动在 Windows Terminal 与重定向场景各跑一次一体化生成。
回滚：恢复 `LiveProgressLine` 相关提交即可，无数据/接口迁移。

## Open Questions

（无）
