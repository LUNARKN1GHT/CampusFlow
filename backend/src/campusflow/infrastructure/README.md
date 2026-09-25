# 基础设施层（待实现）

实现应用层定义的数据库、文件存储、模型、解析器和队列接口。计划按需创建 `db/`、`storage/`、`llm/`、`parsers/`、`queue/`。

选型：SQLAlchemy 2 + psycopg 3 连接 PostgreSQL，pgvector 保存向量；本地私有目录保存原文件；pypdf 读取 PDF 文本，扫描页走可替换 OCR／多模态适配器；RQ + Redis 执行耗时任务。

此目录当前仅定义边界，没有连接数据库或外部服务。供应商凭据仅从后端配置注入，不能暴露到前端或日志。
