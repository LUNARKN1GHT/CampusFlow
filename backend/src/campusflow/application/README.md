# 应用层（待实现）

按用例组织 `materials`、`qa`、`tasks`、`planning` 等模块：协调领域规则、事务与基础设施接口，返回独立于 HTTP 的结果。

数据库、文件、模型和队列接口放在此层的 `ports` 中，具体实现放在 `infrastructure`，由入口装配。仅在真实用例需要时新增接口，不为健康检查创建空 service。

应用层不导入 FastAPI、SQLAlchemy 或供应商 SDK，不直接读环境变量；API 和 Worker 调用同一套用例。
