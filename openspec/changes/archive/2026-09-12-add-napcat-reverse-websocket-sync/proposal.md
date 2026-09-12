## Why

当前采集链路依赖人工导出 QQChatExporter 文件，无法在群聊发生后实时更新画像数据。用户已运行 NapCat 的 OneBot 11 **WebSocket Server**，因此 QQStalker 应作为带 token 的 WebSocket 客户端主动连接该服务，将白名单群的新增消息及已知消息的撤回状态同步到现有 PostgreSQL。

现有功能平铺在 `src/`，文件导入、报告 CLI 和常驻 FastAPI 服务会相互耦合。需要先分离共享数据库能力、既有 CLI 与实时服务，才能复用历史功能并安全扩展实时采集。

## What Changes

- 将代码重组为单一 `pyproject.toml` 管理的 `qqstalker_core`、`qqstalker_cli`、`qqstalker_realtime` 包；旧的平铺 `src.*` 入口和兼容转发模块不再保留。
- 将 SQLModel 实体、数据库连接和通用消息持久化规则移入 `qqstalker_core`，使文件导入与实时同步使用同一 PostgreSQL 表和事务语义。
- 新增 FastAPI 服务，在启动阶段主动连接配置的 NapCat WebSocket Server。首次不可达时立即退出，并显示不含 token 的目标连接参数；首次连接成功后的断线使用指数退避自动重连。
- 仅同步群白名单内的普通群消息，保存文本、数组消息段、群名片、@ 提及和资源元数据，不下载远程附件、不发送 OneBot action。
- 处理群撤回 notice：目标消息已存在时保留正文并标记 `recalled=true`；目标不存在时忽略。
- 实时事件优先按 `group_id` 匹配 `chats.peer_uid`；无法匹配则新建会话并告警，不启发式合并。
- 提供仅本机监听的 `/healthz`、`/readyz`、`/sync/status`，并完善配置、文档和测试。
- 为实时服务提供 VS Code 调试启动配置、容器镜像和 Compose 部署；容器使用服务名访问 PostgreSQL，并通过宿主机网关访问本机 NapCat。

## Capabilities

### New Capabilities

- `napcat-forward-websocket-sync`: FastAPI 主动连接 NapCat OneBot 11 WebSocket Server，按白名单同步群消息与已知消息的撤回状态。
- `multi-package-layout`: 分离共享核心、CLI 和实时服务包，并要求所有调用使用新的模块路径。

### Modified Capabilities

- 无现有 OpenSpec 能力规格。

## Impact

- 所有 `src/` 导入路径、测试导入和 README 命令将调整，但 PostgreSQL 表名和历史数据保持兼容。
- 使用已声明的 FastAPI 运行时实现正向 WS 客户端与本机管理 API。
- NapCat 地址、token 与群白名单为本地敏感配置；token 不得提交、记录或在错误中显示。
- QQChatExporter CLI 继续作为断线窗口的历史补齐手段；本变更不承诺跨采集来源的同一消息去重。
