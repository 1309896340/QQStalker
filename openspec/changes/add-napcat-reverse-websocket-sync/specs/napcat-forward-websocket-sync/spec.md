## Purpose

让 QQStalker 通过 FastAPI 主动连接 NapCat 的 OneBot 11 WebSocket Server，将白名单群聊的新增消息和已知消息的撤回状态实时、幂等地写入既有 PostgreSQL，供导出和画像流程使用。

## ADDED Requirements

### Requirement: Initial NapCat connection gates service startup
系统 SHALL 在 FastAPI 初始化阶段读取 NapCat WebSocket 地址、token、群白名单和管理 API 配置，并主动建立认证连接。首次连接失败时系统 MUST 终止启动，并显示协议、主机、端口和路径等非敏感连接参数；token 不得出现在日志、异常或 HTTP 响应中。数据库不可用时系统也不得进入就绪状态。

#### Scenario: Initial connection succeeds
- **WHEN** 数据库可用且目标 NapCat WebSocket Server 可达
- **THEN** 服务完成启动、建立认证连接并进入已连接状态

#### Scenario: Initial NapCat connection is unreachable
- **WHEN** 服务初始化时无法连接目标 NapCat WebSocket Server
- **THEN** 服务终止启动并输出不含 token 的连接不可达说明和目标参数

### Requirement: Local operational API
系统 SHALL 提供 `/healthz`、`/readyz` 和 `/sync/status`，并 MUST 默认仅绑定回环地址。`/healthz` 表示进程存活；`/readyz` 仅在数据库可用且 NapCat 已连接时表示就绪；`/sync/status` 返回不含聊天内容或 token 的连接状态、连接变化、计数和错误摘要。

#### Scenario: Reconnecting service is not ready
- **WHEN** 已完成首次连接的服务与 NapCat 断开并处于重连状态
- **THEN** `/healthz` 表示存活、`/readyz` 表示未就绪，且状态端点提供非敏感断线摘要

### Requirement: Whitelisted group-message synchronization
系统 SHALL 仅同步有效的 OneBot `post_type=message`、`message_type=group` 事件，且其 `group_id` 必须处于显式配置白名单内。系统 MUST 在单个事务中保存消息时间、来源标识、发送者、群名片、文本、数组消息段、@ 提及和可用资源元数据。空白名单 MUST 拒绝全部群消息；非群、非白名单、畸形和不支持事件 SHALL 被忽略且不得产生部分写入。

#### Scenario: Whitelisted message is persisted
- **WHEN** NapCat 发送有效的白名单群普通消息
- **THEN** 系统创建或更新会话、参与者和群成员关系，并保存消息和可用明细

#### Scenario: Empty whitelist is deny-all
- **WHEN** 群白名单未配置或为空
- **THEN** 系统不持久化任何群消息并报告未允许群的状态

#### Scenario: Remote media is not downloaded
- **WHEN** 有效消息含有图片、文件或其他远程资源段
- **THEN** 系统仅保存可用元数据，不下载或存储远程附件字节

### Requirement: Shared database identity and replay handling
系统 SHALL 与 QQChatExporter CLI 共用现有 PostgreSQL 表和查询能力。实时服务 MUST 以 `str(group_id)` 匹配 `chats.peer_uid`，匹配时复用会话；未匹配时创建新群会话并记录告警。相同会话和相同来源消息标识的重复实时事件 MUST 只保留一条消息及附属记录。

#### Scenario: Matching group reuses existing history
- **WHEN** 白名单群的 `group_id` 与既有 `peer_uid` 匹配
- **THEN** 实时消息写入该既有会话并可被原有导出和画像流程查询

#### Scenario: Unmatched group is explicit
- **WHEN** 白名单群没有匹配的既有 `peer_uid`
- **THEN** 系统创建以群号为稳定标识的新会话并记录来源映射告警

#### Scenario: Replayed event is skipped
- **WHEN** 系统再次收到同一群的同一来源消息事件
- **THEN** 系统不创建第二条消息、元素、资源或提及记录，并增加重复计数

### Requirement: Recall handling preserves known content
系统 SHALL 处理群撤回 notice。若通知指向的消息已存在于相同群会话，系统 MUST 保留既有正文和明细并将该消息标记为已撤回；若目标消息不存在，系统 SHALL 忽略 notice 且不创建占位消息。

#### Scenario: Known message is marked recalled
- **WHEN** 收到指向本地已存在群消息的有效撤回 notice
- **THEN** 系统保留该消息内容并更新其撤回状态

#### Scenario: Missing message recall is ignored
- **WHEN** 断线重连后或其他情况下收到目标不在本地库中的撤回 notice
- **THEN** 系统不创建消息、成员或占位记录，并增加忽略计数

### Requirement: Post-connection reconnection and private operations
系统 SHALL 仅在至少一次成功连接后，对后续断线使用有界、带抖动的指数退避自动重连。系统 SHALL 记录连接、断开、接收、忽略、重复和失败状态，但 MUST 不记录 token、完整事件、正文或资源 URL。连接中断期间的历史补齐 SHALL 由离线 CLI 负责，不属于实时服务承诺。

#### Scenario: Reconnected service resumes new events
- **WHEN** 已成功连接的 NapCat 连接断开后恢复可达
- **THEN** 服务重连并继续同步重连后收到的新事件，不声称补齐断线窗口
