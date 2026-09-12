## Purpose

将 QQStalker 的共享数据能力、既有命令行工具和实时服务划分为明确包边界，使两种采集方式共享数据库语义，并为现有 CLI 提供迁移期兼容性。

## ADDED Requirements

### Requirement: Single-project multi-package source layout
系统 SHALL 在根 `pyproject.toml` 下划分 `qqstalker_core`、`qqstalker_cli` 和 `qqstalker_realtime` 包。共享模型、数据库和持久化 MUST 位于 core；文件导入、导出、分析和渲染 MUST 位于 cli；FastAPI 与 NapCat 集成 MUST 位于 realtime。应用包不得反向成为共享层依赖。

#### Scenario: Both collectors share persistence semantics
- **WHEN** CLI 导入器和实时服务写入会话、参与者或消息
- **THEN** 两者使用 core 的同一模型与持久化边界

#### Scenario: Real-time service starts independently
- **WHEN** 操作员启动实时服务
- **THEN** 服务无需执行文件导入、分析或渲染 CLI 即可运行

### Requirement: CLI migration compatibility
系统 SHALL 为当前 `src.import_export`、`src.export_markdown`、`src.analyze_transcript`、`src.render_html_png` 和 `src.generate_portrait` 提供一个发布周期的兼容转发入口。兼容入口 MUST 调用迁移后的实现并显示迁移提示；新文档 MUST 使用新的 CLI 包路径。原有 CLI 的可观察行为不得因重构改变。

#### Scenario: Existing command remains usable
- **WHEN** 操作员运行当前形式的 `python -m src.<command>`
- **THEN** 命令完成等价操作并显示迁移提示，而不会因模块移动失败

#### Scenario: New package command works
- **WHEN** 操作员按 README 的新 CLI 包路径执行同一命令
- **THEN** 命令无需兼容包装器即可成功运行
