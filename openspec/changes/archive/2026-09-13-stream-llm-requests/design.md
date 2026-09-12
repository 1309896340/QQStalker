## Context

`request_portraits`（`src/qqstalker_cli/analyze_transcript.py`）目前以非流式 `client.post` 调用 OpenAI 兼容端点，单一超时值同时作用于连接与读取。画像批次、语录精选、群像速览与讨论纪要全部经由这一个函数发起请求；上一变更已为其加入阶段标签、耗时日志与 15 秒心跳。动机与失败模式见 proposal.md - Why，行为契约见 specs/llm-streaming-transport/spec.md。

## Goals / Non-Goals

**Goals:**

- 单一请求函数内完成流式/非流式切换，所有上层调用方零改动获得流式能力。
- SSE 解析逻辑为纯函数，可脱离网络做单元测试。
- 流式接收期间由真实数据到达驱动进度输出，替代后台线程心跳。
- 提供绕过完整画像流程的独立调试命令，可用真实样本端到端验证。

**Non-Goals:**

- 不改变批次划分、上下文构建、成员补偿等画像流程逻辑。
- 不引入异步（`httpx.AsyncClient`）或并发请求。
- 不做 SSE 断点续传（OpenAI 兼容协议不支持，重试即整次重发）。
- 不抽取独立的 LLM 客户端包（保持 `analyze_transcript.py` 内聚，见 Open Questions）。

## Decisions

### D1：传输实现留在 `request_portraits`，SSE 解析拆为纯函数

`request_portraits` 内部按回退开关选择流式或非流式路径；新增纯函数 `parse_sse_chunk`/增量累积器负责单行 `data:` 载荷到文本/结束原因的归并，不接触网络。理由：所有调用方（含 `generate_portrait.py` 与讨论纪要回调）已经经由该函数，改动收敛在一处；纯函数化让 SSE 格式差异（keep-alive 注释行、末尾 usage 块）可以逐行做表驱动测试。替代方案（抽出 `llm_client.py` 模块）改动面更大且当前只有一个消费者，暂不采用。

### D2：流式路径用 `client.stream` + `iter_lines`，逐行驱动进度

以 `client.stream("POST", endpoint, ...)` 进入流式上下文，`response.iter_lines()` 逐行消费：跳过空行与 `:` 开头的 SSE 注释（keep-alive）；遇到 `data: [DONE]` 结束；其余 `data:` 载荷按 JSON 解析，累积 `choices[0].delta.content`（兼容 `reasoning_content` 增量），记录最后一个非空 `finish_reason`；无 `choices` 的块（如 usage 末块）直接跳过。进度由数据到达驱动：距上次输出超过 `progress_interval_seconds` 时打印"已接收 N 字（已等待 …）"，无需后台线程；现有 `llm_wait_heartbeat` 仅保留给非流式回退路径。

### D3：超时语义依赖 httpx 既有行为，不改配置

继续向 `httpx.Client` 传入单一 `timeout_seconds`。httpx 的读超时在流式下天然作用于相邻两次读取之间——即增量间隔超时，这正是规格"流式超时语义"的要求；非流式下它仍是整次响应的读超时。用户无需理解新配置。

### D4：断流归类为可重试传输错误，诊断行只含字符数

流中途抛出的 `httpx.TransportError`（含 `RemoteProtocolError`、读超时）复用现有指数退避重试，错误信息附带"已接收 N 字后中断"的诊断；诊断与进度输出只含字符数与时长，不打印生成内容或密钥。流正常结束但 `finish_reason == "length"` 时完全走既有截断处理与按成员补偿，不追加重试。连接建立阶段的 HTTP 状态错误分类（含 Ark Agent Plan 提示）不变。

### D5：`LLM_STREAM` 布尔开关，默认开启

新增 `boolean_setting` 环境变量解析（接受 `1/true/yes/on` 与 `0/false/no/off`，缺省视为开启，非法值报清晰错误）。关闭时走原非流式路径——这同时是回滚手段。`.env.example` 补充说明。

### D6：调试入口为独立模块 `stream_debug.py`，复用现有构建函数

`python -m src.qqstalker_cli.stream_debug [--input <markdown>] [--env-file <path>] [--top-members N] [--min-message-count N]`：复用 `load_dotenv`、`extract_member_messages`、`select_members`、`batch_members`、`build_member_contexts`、`build_member_prompt` 构建第一个真实批次请求，以阶段标签"流式调试"调用 `request_portraits`，结束时输出耗时、接收字符数、结束原因、重试次数与内容首尾预览（各 200 字，控制台输出）。未传 `--input` 时默认 `exports/20260911194123_消息记录.md`，文件缺失在任何请求发起前报错退出。不生成 HTML、不写任何文件。

### D7：进度在交互终端单行原位刷新，重定向时逐行回退

新增 `LiveProgressLine` 行内进度管理器：构造时检测 `sys.stdout.isatty()`；交互终端输出 `\r` + `\x1b[2K`（回车 + 整行擦除）原位刷新，Windows 用 `os.system("")` 一次性启用 VT 处理；非 TTY（文件、管道、被工具捕获）逐行累加，与刷新模式共用同一段文案，保证日志可读。管理器持有锁并跟踪"活跃行"状态：重试日志、完成日志、异常路径打印前先 `end()` 结束当前刷新行，避免普通日志拼接在进度帧之后；非流式等待心跳线程接入同一管理器。刷新模式下有效间隔缩短为 1 秒（同行覆写无噪音代价），重定向模式维持配置间隔。替代方案（`rich` 进度条）因仅为单行刷新引入运行时依赖而否决。

## Risks / Trade-offs

- [各提供商 SSE 细节不一（注释行、usage 末块、reasoning 增量、个别厂商无 `[DONE]`）] → 解析器只认 `data:` 载荷并对无法识别的结构宽松跳过；流以连接关闭自然终结兜底；表驱动单测覆盖已知变体。
- [Ark Agent Plan 等端点可能不支持 `stream: true`] → `LLM_STREAM=false` 一键回退；若流式请求返回明确 HTTP 错误，错误信息沿用现有格式化逻辑提示。
- [进度由数据驱动，首 token 前仍有一段静默（模型思考阶段可能数十秒无增量）] → 保留阶段开始日志；首 token 超过读超时才会失败，与"服务端停滞触发超时"语义一致。
- [现有 `LlmErrorTests` 以 `client.post` 为 mock 切点] → 非流式路径测试保留；流式路径以 `client.stream` 为切点新增用例，两套各自独立。
- [重试整次重发，长生成失败一次的成本仍然存在] → 接受；协议无续传，且流式大幅降低触发概率。

## Migration Plan

实施完成后默认即流式，无需数据迁移；`.env` 不需改动即可运行。回滚 = 设置 `LLM_STREAM=false`（行为回到现状），或 revert 提交。

## Open Questions

- 是否在后续将 `request_portraits` 及 SSE 解析抽成独立 `llm_client` 模块（当 `qqstalker_realtime` 也需要调用大模型时再评估，不影响本变更）。
