# QQStalker

QQStalker 用于将 QQChatExporter 导出的聊天记录导入 PostgreSQL，按日期导出 Markdown，并基于导出的记录生成群成员画像和可分享的 PNG 图片。项目提供独立的 Python 命令行脚本，适合在 Windows PowerShell 中按需运行。

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
uv run python -m src.import_export C:\exports\my-chat
```

按日期导出 Markdown 消息记录；`--end-date` 可选，省略时只导出当天：

```powershell
uv run python -m src.export_markdown 2026-09-01 .\exports --end-date 2026-09-07
```

使用本地 LLM 配置生成成员画像 HTML：

```powershell
uv run python -m src.analyze_transcript .\exports\2026-09-01.md .\analysis
```

将生成的 HTML 渲染为 PNG；第二个参数为输出目录，不存在时自动创建。图片将以
`YYYYmmddHHMMSS_群员画像.png` 命名，超出最大高度时会自动生成带 `_1`、`_2` 等后缀的分片：

```powershell
uv run python -m src.render_html_png .\analysis\portrait.html .\analysis
```

## 工具说明

| 脚本 | 用途 |
| --- | --- |
| `src/import_export.py` | 验证并同步 QQChatExporter JSON、消息和图片资源至 PostgreSQL。 |
| `src/export_markdown.py` | 从数据库按日期范围生成 Markdown 消息记录。 |
| `src/analyze_transcript.py` | 调用兼容 OpenAI Chat Completions 的 LLM，为记录中的每位成员生成画像 HTML。 |
| `src/render_html_png.py` | 通过 Playwright 将 HTML 渲染为高分辨率 PNG。 |
| `src/parse_export.py` | 快速检查 JSON 导出文件及资源目录，便于排查导出格式。 |

查看任一工具的完整参数：

```powershell
uv run python -m src.import_export --help
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
