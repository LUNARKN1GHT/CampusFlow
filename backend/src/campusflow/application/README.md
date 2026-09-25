# 应用层

按用例组织模块，协调领域规则、事务与基础设施接口，返回独立于 HTTP 的结果。

当前用例模块：

- `courses.py`：学期与课程的增改查、归档
- `tasks.py`：任务增改查与执行进度流转
- `schedule.py`：固定日程与可用时间的增改查删

`ports/repositories.py` 定义仓储协议、数据传输对象（DTO）与事务控制接口，具体实现位于 `infrastructure/repositories.py`，由 API 依赖注入装配。

约定：

- 应用层不导入 FastAPI、SQLAlchemy 或供应商 SDK，不直接读环境变量；API 与 Worker 调用同一套用例。
- `update_*` 用例接收"合并后的最终值"（API 层把补丁与当前值合并后传入），`None` 表示字段确实要清空。
- 事务提交（`repos.uow.commit()`）在用例内控制；校验失败抛出 `domain.errors` 中的错误，由 API 层转换为 HTTP 响应。
- 后续 `materials`、`qa`、`planning` 用例按真实需求在对应里程碑加入，不预先创建空模块。
