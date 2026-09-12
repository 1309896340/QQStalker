## Context

现有代码平铺在 `src/`，`import_export.py` 同时负责数据库连接、QQChatExporter 解析、实体写入和 CLI。`messages` 已有 `(chat_id, external_id)` 唯一约束，`Message.recalled` 已可标记撤回；但没有常驻服务、NapCat 适配器或运行状态 API。FastAPI 已是项目依赖。

用户运行的是 NapCat 的 WebSocket Server，故本变更取代原提案的反向 WS 假设：FastAPI 作为 WebSocket 客户端连接 NapCat，首次连接是服务启动前置条件。

## Goals / Non-Goals

**Goals:**

- 用单一 `pyproject.toml` 分离核心、CLI 和实时服务。
- FastAPI lifespan 先验证 PostgreSQL 和 NapCat 的首次连接，再接收 OneBot 群事件。
- 复用既有表，实时写入消息及其撤回状态。
- 将白名单、状态端点、重连和敏感日志边界设为可测试行为。

**Non-Goals:**

- 不改变 NapCat 为反向 WS，不发送 QQ action，不自动补历史。
- 不下载附件，不同步私聊、成员变更或无关事件，不处理缺失目标的撤回。
- 不做跨来源启发式去重、多个 `pyproject.toml`、远程管理 API 或 Web UI。

## Decisions

### 1. 三包结构

```text
src/
  qqstalker_core/       # models, database, normalized inputs, persistence
  qqstalker_cli/        # import, export, analysis, rendering
  qqstalker_realtime/   # FastAPI, OneBot schemas, NapCat WS client
```

core 仅提供模型、连接与事务 API；CLI 和 realtime 为平级消费者。旧的平铺模块和兼容包装器直接移除，README 与 VS Code 调试配置统一使用新模块路径。保留平铺模块并只加服务会继续耦合文件导入细节，故不采用；拆成独立 Python 项目会增加共享模型维护成本，也不采用。

### 2. FastAPI lifespan 和正向 WS 客户端

app factory 的 lifespan 依序加载环境配置、验证白名单和回环管理监听、检查数据库、连接 `ws://` / `wss://` NapCat 地址，并在握手中携带 token。首次失败抛出启动异常，显示净化后的 URL；首次成功后以受管后台任务读取事件。断开则状态未就绪，并以 1 秒起步、最大 30 秒、带抖动的指数退避重连。关闭时取消任务、关闭连接。

不采用首次失败后仍保持服务运行的策略，因为用户要求 NapCat 不可达时进程直接终止。

### 3. 最小本机管理面和状态对象

Uvicorn 仅绑定 `127.0.0.1`。进程内状态保存连接状态、首次成功和最近状态变化、最近成功同步、分类计数和净化后的最后错误。`/healthz` 仅表示进程存活；`/readyz` 要求数据库与 WS 均可用；`/sync/status` 只显示非敏感状态。

### 4. OneBot 适配和撤回语义

新 Pydantic 模型要求 NapCat 采用数组消息格式。适配器只接收普通群消息与群撤回 notice：普通消息必须有消息 ID、群号、发送者和时间，且通过白名单；消息段保留文本、@ 与资源元数据，不下载附件。撤回 notice 以 `(group_id, message_id)` 查找已保存消息，存在则仅更新 `recalled`，不存在即忽略。

### 5. 共享持久化和可靠性边界

core 公开不依赖文件或 WS 的标准化消息持久化服务。每个事件使用短事务创建或更新会话、参与者、成员关系，检查唯一键后写入消息明细；实时消息 `import_batch_id` 为空。`str(group_id)` 匹配 `Chat.peer_uid`，否则新建带群号占位名的会话并告警。当前仅支持一个 NapCat 账号。

语义为“连接期间至少处理一次、提交幂等”，不是恰好一次。离线期由 QQChatExporter CLI 补齐；不使用正文或时间猜测跨来源相同消息。

### 6. 调试和 Compose 部署

VS Code 调试配置以 `src.qqstalker_realtime` 模块启动服务，并使用 `.env`。现有 `database/docker-compose.yml` 扩展为同时编排 PostgreSQL 与 `realtime` 服务：实时容器以 `postgres:5432` 连接数据库，默认以 `host.docker.internal`（可由 `NAPCAT_DOCKER_WS_HOST` 覆盖）访问运行在 Docker Desktop 宿主机的 NapCat。镜像仅安装从 `uv.lock` 提取并固定版本的实时运行时依赖，避免携带画像渲染的 GUI 组件。状态 API 的端口映射固定绑定到宿主机 `127.0.0.1`；PostgreSQL 卷继续沿用该 Compose 项目已有卷。

## Risks / Trade-offs

- [首次连接失败即退出] → 显示非机密地址参数，NapCat 恢复后由操作者重启服务。
- [断线缺口] → 保留 CLI 补齐；缺失目标的撤回有意忽略，避免伪造内容。
- [历史 `peer_uid` 与群号不一致] → 新建会话并告警，不做高风险合并。
- [重构破坏脚本] → README、VS Code 调试配置和测试统一使用新入口；旧入口明确不再支持。
- [token 或正文泄漏] → `.env` 读取、净化错误、拒绝记录事件正文 / URL，管理面回环绑定。
- [容器无法访问宿主机 NapCat] → 使用独立的 `NAPCAT_DOCKER_WS_HOST`，默认 Docker Desktop 网关，避免复用容器内的 `127.0.0.1`。

## Migration Plan

1. 创建三包，迁移测试、README 和 VS Code 调试配置至新 CLI 入口，并删除旧模块。
2. 抽取共享数据库 / 持久化层，确认表名和历史数据不变。
3. 实现 FastAPI 预检、正向 WS 客户端、状态 API 与 OneBot fixtures。
4. 在受控群验证首次失败、正常消息、白名单、撤回、断线重连和事务回滚。
5. 启动 PostgreSQL 后核验存量 `peer_uid` 与目标群号；不匹配时接受新会话和告警。
6. 回滚时停止服务；既有 CLI、库和已同步消息不自动删除。
7. Compose 部署可使用 `down` 停止实时服务且保留 PostgreSQL 数据卷。

## Open Questions

- OneBot `message_id` 是否与 QQChatExporter `QQMessage.id` 稳定对应仍需用脱敏样本验证；这只影响未来跨来源去重，不改变本变更范围。
