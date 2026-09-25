# 基础设施层

实现应用层定义的接口。ORM 模型只在本层使用，不直接返回给前端。

当前实现：

- `db/`：SQLAlchemy 2 引擎与会话工厂（同步 Session）、ORM 模型（六张业务表）
- `repositories.py`：`application/ports/repositories.py` 的 SQLAlchemy 实现

后续按里程碑加入：

- `storage/`：本地私有目录保存原文件（M2 资料功能）
- `parsers/`：pypdf 读取 PDF 文本，扫描页走可替换 OCR／多模态适配器（M2）
- `llm/`：模型适配器（视觉识别、结构化提取、对话、embedding）（M2/M3）
- `queue/`：RQ + Redis 执行耗时任务（M2）

约定：供应商凭据仅从后端配置注入，不能暴露到前端或日志；Fake 适配器供测试与开发期使用，真实模型调用仅用于评测与验收。
