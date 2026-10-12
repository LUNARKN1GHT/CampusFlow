# Worker 入口

RQ Worker 与 API 共用应用层解析用例，消息只传作业编号。当前接入文本、PDF、扫描页回退、图片识别和表格关联；事项提取与向量索引仍待后续任务。

Linux 从 backend 运行 `uv run --locked python -m campusflow.workers.main`；Windows 使用 Docker。诊断入口为 `python -m campusflow.workers.diagnostics`，只读状态并省略敏感数据。

启动配置、共享存储、部分失败与安全重试步骤见 [资料处理运行手册](../../../../docs/MATERIAL_PROCESSING.md)。业务状态以 PostgreSQL 为准，不以 RQ 成功日志代替完整资料验收。
