## Purpose

将 QQStalker 的共享数据能力、既有命令行工具和实时服务划分为明确包边界，使两种采集方式共享数据库语义，并使用唯一、明确的 CLI 模块入口。

## ADDED Requirements

### Requirement: Single-project multi-package source layout
系统 SHALL 在根 `pyproject.toml` 下划分 `qqstalker_core`、`qqstalker_cli` 和 `qqstalker_realtime` 包。共享模型、数据库和持久化 MUST 位于 core；文件导入、导出、分析和渲染 MUST 位于 cli；FastAPI 与 NapCat 集成 MUST 位于 realtime。应用包不得反向成为共享层依赖。

#### Scenario: Both collectors share persistence semantics
- **WHEN** CLI 导入器和实时服务写入会话、参与者或消息
- **THEN** 两者使用 core 的同一模型与持久化边界

#### Scenario: Real-time service starts independently
- **WHEN** 操作员启动实时服务
- **THEN** 服务无需执行文件导入、分析或渲染 CLI 即可运行

### Requirement: CLI uses only its migrated package paths
系统 SHALL 仅从 `src.qqstalker_cli` 提供文件导入、导出、分析和渲染命令。系统 MUST NOT 提供 `src.import_export`、`src.export_markdown`、`src.analyze_transcript`、`src.render_html_png`、`src.generate_portrait` 或 `src.parse_export` 的兼容转发模块。README 和 VS Code 调试配置 MUST 使用新的 CLI 包路径。

#### Scenario: New package command works
- **WHEN** 操作员按 README 的新 CLI 包路径执行同一命令
- **THEN** 命令无需兼容包装器即可成功运行

#### Scenario: Legacy command is unavailable
- **WHEN** 操作员运行旧的 `python -m src.<command>` 形式
- **THEN** Python 找不到该旧模块，且不会再运行兼容转发逻辑
