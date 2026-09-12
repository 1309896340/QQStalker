## 1. SSE 解析与环境开关基础

- [x] 1.1 在 `analyze_transcript.py` 实现 SSE 行解析纯函数（`data:` 载荷、`[DONE]` 结束、空行与 `:` 注释行跳过、`delta.content` 与 `reasoning_content` 增量累积、最后一个非空 `finish_reason` 记录、无 `choices` 块跳过），并以表驱动单测覆盖 keep-alive 注释、usage 末块、无 `[DONE]` 以连接关闭结束等变体；验证 `uv run python -m unittest tests.test_analyze_transcript` 通过
- [x] 1.2 新增 `boolean_setting` 环境变量解析助手（接受 `1/true/yes/on` 与 `0/false/no/off`，缺省返回默认值，非法值报清晰错误），单测覆盖合法值、缺省与非法值三类输入

## 2. 流式传输路径

- [x] 2.1 在 `request_portraits` 增加流式分支（`client.stream` + `iter_lines` 接入 1.1 解析器），进度改为数据到达驱动、按 `progress_interval_seconds` 节流输出"已接收 N 字"，进度行不含内容与密钥；以 mock `client.stream` 的单测验证正常流式往返（累积文本、finish_reason、请求/完成日志）
- [x] 2.2 断流处理：流中途 `TransportError` 归入现有可重试分类，错误信息附带"已接收 N 字后中断"诊断；重试成功以完整结果返回；`finish_reason == "length"` 时不追加重试、沿用既有截断与成员补偿路径；单测覆盖中途断流重试成功与流式截断两个场景
- [x] 2.3 接入 `LLM_STREAM` 开关（默认开启；关闭时走原非流式路径并保留等待心跳），更新 `.env.example` 说明；单测验证开关两态分别命中 `client.stream` 与 `client.post`
- [x] 2.4 回归验证既有测试：`LlmErrorTests`（非流式 mock 路径）与 `ProgressReportingTests` 全部通过，必要时按流式语义调整断言

## 3. 独立流式调试入口

- [x] 3.1 新建 `src/qqstalker_cli/stream_debug.py`（`python -m src.qqstalker_cli.stream_debug`）：参数 `--input`（默认 `exports/20260911194123_消息记录.md`）、`--env-file`、`--top-members`、`--min-message-count`；复用既有函数构建第一个真实批次请求并以阶段标签"流式调试"发起调用，结束时输出耗时、接收字符数、结束原因、重试次数与首尾各 200 字预览；不生成 HTML、不写任何文件；样本缺失时在任何请求前报错退出；以 mock `request_portraits` 的单测覆盖默认样本命中与样本缺失两场景
- [x] 3.2 用真实样本与本地 `.env` 人工运行调试入口一次，确认控制台出现阶段日志、增量进度行与结果摘要，且不产生报告文件；在无真实端点环境下至少验证构建路径与缺参报错

## 4. 验证与收尾

- [x] 4.1 运行 `uv run pyright`、`uv run python -m compileall -q src`、`uv run python -m unittest` 全量通过
- [x] 4.2 运行 `openspec validate stream-llm-requests --strict` 通过

## 5. 进度单行刷新（并入）

- [x] 5.1 新增 `LiveProgressLine` 行内进度管理器（TTY 检测、`\r` + ANSI 清行、Windows VT 启用、重定向逐行回退、线程安全、`end()` 结束活跃行），流式进度与等待心跳接入，重试/完成/异常路径先结束刷新行；刷新模式有效间隔 1 秒；单测覆盖终端刷新控制序列、重定向回退、心跳接入与流式请求终端模式四场景；验证 `uv run python -m unittest tests.test_analyze_transcript` 通过
- [x] 5.2 重定向运行流式调试入口确认逐行回退且日志无控制字符；交互终端刷新的控制序列由单测断言覆盖，最终视觉效果由用户在自己终端运行调试入口确认
