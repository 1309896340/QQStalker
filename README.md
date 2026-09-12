# QQStalker

QQStalker 用于将 QQChatExporter 导出的聊天记录导入 PostgreSQL，按日期导出 Markdown，并基于导出的记录生成群成员画像和可分享的 PNG 图片。它也可以作为 NapCat OneBot 11 WebSocket Server 的客户端，将白名单群的普通消息实时同步到同一数据库。

## 前置条件

- Python 3.12（项目由 `.python-version` 固定）
- [uv](https://docs.astral.sh/uv/)
- Docker Desktop（仅在使用 PostgreSQL 导入功能时需要）

安装依赖：

```powershell
uv sync
```

复制 `.env.example` 为本地 `.env`，再填写 PostgreSQL 密码和 LLM API 配置。`.env` 包含敏感信息，已被 Git 忽略，切勿提交或分享。

## 快速开始

启动本地数据库：

```powershell
docker compose -f database/docker-compose.yml up -d
```

从 QQChatExporter 归档目录导入数据。目录应包含一个 JSON 导出文件及其 `resources/` 资源目录：

```powershell
uv run python -m src.qqstalker_cli.import_export C:\exports\my-chat
```

按日期导出 Markdown 消息记录；`--end-date` 可选，省略时只导出当天：

```powershell
uv run python -m src.qqstalker_cli.export_markdown 2026-09-01 "群名" .\exports --end-date 2026-09-07
```

使用本地 LLM 配置生成成员画像 HTML：

```powershell
uv run python -m src.qqstalker_cli.analyze_transcript .\exports\2026-09-01.md .\analysis
```

画像末尾会附加“群聊高质量语录精选”专题，默认目标为 8 条；使用 `--quote-count` 可调整数量：

```powershell
uv run python -m src.qqstalker_cli.analyze_transcript .\exports\2026-09-01.md .\analysis --quote-count 12
```

将生成的 HTML 渲染为 PNG；第二个参数为输出目录，不存在时自动创建。图片将以
`YYYYmmddHHMMSS_群员画像.png` 命名，超出最大高度时会自动生成带 `_1`、`_2` 等后缀的分片：

```powershell
uv run python -m src.qqstalker_cli.render_html_png .\analysis\portrait.html .\analysis
```

若要将连续分片横向拼接，可加上 `--stitch-horizontal`；每张拼接图片默认包含 4 个分片，
可用 `--stitch-count` 调整：

```powershell
uv run python -m src.qqstalker_cli.render_html_png .\analysis\portrait.html .\analysis --stitch-horizontal --stitch-count 2
```

如需从数据库消息记录直接生成最终 PNG，可使用整合命令；Markdown 中间文件会自动清理，
分析生成的 HTML 与 PNG 会保留在输出目录（例如 `analysis/`）中，并使用
`YYYYmmddHHMMSS_<群名>.html`、`YYYYmmddHHMMSS_<群名>.png` 命名：

```powershell
uv run python -m src.qqstalker_cli.generate_portrait 2026-09-11 "群名" .\analysis
```

### LLM 超时与重试

`.env` 中的 `LLM_TIMEOUT_SECONDS` 控制单个模型请求等待响应的最长秒数，默认 `300`。较慢的模型或较长的聊天记录可提高到 `900`。网络超时、限流（HTTP 429）及服务端临时错误（HTTP 5xx）会自动重试；`LLM_MAX_RETRIES` 是额外重试次数，`LLM_RETRY_DELAY_SECONDS` 是首次等待秒数，后续每次等待翻倍。示例：

```dotenv
LLM_TIMEOUT_SECONDS=900
LLM_MAX_RETRIES=3
LLM_RETRY_DELAY_SECONDS=2
```

### 思考模式与讨论纪要并发

讨论纪要流水线（分段识别议题 → 归并重复主题 → 逐题撰写纪要）会发起多次模型请求；若使用 GLM、豆包等默认开启思维链的模型，不可见的思维链输出通常是等待时间的主要来源。在 `.env` 中设置 `LLM_THINKING=disabled` 可通过请求中的 `thinking` 字段关闭思维链（仅对支持该字段的服务端生效，留空则不注入）；`LLM_TIMEOUT_SECONDS` 也因此可以保持在默认值。相互独立的纪要请求（分段识别、逐题撰写）默认最多 `4` 个并发，可用 `LLM_DISCUSSION_CONCURRENCY` 调整：

```dotenv
LLM_THINKING=disabled
LLM_DISCUSSION_CONCURRENCY=4
```

## 实时同步（NapCat）

NapCat 必须已启用 OneBot 11 的 **WebSocket Server**。QQStalker 会主动连接该地址，不使用 NapCat 的反向 WebSocket，也不会发送任何 OneBot action。把下列配置写入本地 `.env`；其中 token 是凭据，不能提交、截图或写入日志：

```dotenv
NAPCAT_WS_SCHEME=ws
NAPCAT_WS_HOST=127.0.0.1
NAPCAT_WS_PORT=3001
NAPCAT_WS_PATH=/
NAPCAT_WS_TOKEN=仅填写本机 NapCat token
NAPCAT_ALLOWED_GROUP_IDS=123456789,987654321
NAPCAT_API_HOST=127.0.0.1
NAPCAT_API_PORT=8010
```

`NAPCAT_ALLOWED_GROUP_IDS` 是强制白名单；未配置或留空时会拒绝所有群消息。服务只保存白名单群的普通群消息、数组消息段、群名片、@ 提及和附件元数据，不下载附件字节。群撤回通知只会把本地已存在的对应消息标记为撤回并保留原正文；断线期间缺失的消息或撤回不会被伪造。

启动实时服务：

```powershell
uv run python -m src.qqstalker_realtime
```

启动时会先检查 PostgreSQL，再连接 NapCat。首次无法连接时进程会立即退出，并仅显示协议、主机、端口和路径，不显示 token。首次连接成功后的断线会以 1 秒起步、最大 30 秒且带抖动的指数退避重连。重连只处理恢复后收到的新事件；断线窗口仍应通过 QQChatExporter CLI 导入补齐。

管理端点仅监听回环地址：`/healthz` 表示进程存活，`/readyz` 要求 PostgreSQL 和 NapCat 均可用，`/sync/status` 显示非敏感连接状态与计数。例如：

```powershell
Invoke-RestMethod http://127.0.0.1:8010/healthz
Invoke-RestMethod http://127.0.0.1:8010/readyz
Invoke-RestMethod http://127.0.0.1:8010/sync/status
```

### Docker Compose 部署

若要将 PostgreSQL 与实时服务一起部署，使用仓库已有的 Compose 文件：

```powershell
docker compose -f database/docker-compose.yml up -d --build
docker compose -f database/docker-compose.yml logs -f realtime
```

在 Docker Desktop 上，实时容器不能通过 `127.0.0.1` 访问运行在宿主机的 NapCat。Compose 会自动将 `NAPCAT_WS_HOST` 改为 `NAPCAT_DOCKER_WS_HOST`，其默认值为 `host.docker.internal`；只有 NapCat 不在宿主机时才需在 `.env` 修改该变量。PostgreSQL 数据继续使用原有 Compose 卷，管理 API 仍只发布到宿主机 `127.0.0.1`。

停止服务而保留数据库数据：

```powershell
docker compose -f database/docker-compose.yml down
```

## 包布局

核心模型、数据库连接和通用持久化位于 `src.qqstalker_core`；文件导入、导出、分析和渲染 CLI 位于 `src.qqstalker_cli`；FastAPI/NapCat 服务位于 `src.qqstalker_realtime`。旧的 `python -m src.<command>` 入口已移除；请使用本文的新模块路径。

VS Code 可从“运行和调试”中选择“NapCat 实时同步服务”来加载本地 `.env` 并启动同一服务。

## 工具说明

| 脚本 | 用途 |
| --- | --- |
| `src.qqstalker_cli.import_export` | 验证并同步 QQChatExporter JSON、消息和图片资源至 PostgreSQL。 |
| `src.qqstalker_cli.export_markdown` | 从数据库按日期范围生成 Markdown 消息记录。 |
| `src.qqstalker_cli.analyze_transcript` | 调用兼容 OpenAI Chat Completions 的 LLM，为记录中的每位成员生成画像 HTML。 |
| `src.qqstalker_cli.render_html_png` | 通过 Playwright 将 HTML 渲染为高分辨率 PNG。 |
| `src.qqstalker_cli.generate_portrait` | 串联消息导出、LLM 分析与 PNG 渲染；消息记录使用临时目录，画像 HTML 保留在输出目录。 |
| `src.qqstalker_cli.parse_export` | 快速检查 JSON 导出文件及资源目录，便于排查导出格式。 |
| `src.qqstalker_realtime` | 主动连接 NapCat、同步白名单群消息并提供本机状态 API。 |

查看任一工具的完整参数：

```powershell
uv run python -m src.qqstalker_cli.import_export --help
```

首次使用 PNG 渲染前，如 Playwright 尚未安装浏览器，执行：

```powershell
uv run playwright install chromium
```

## 开发检查

```powershell
uv run pyright
uv run python -m compileall -q src
```

请只使用脱敏的聊天导出进行测试。成员画像属于模型推断结果，应结合原始对话审慎解读，避免将其视为事实或传播涉及他人隐私的内容。
