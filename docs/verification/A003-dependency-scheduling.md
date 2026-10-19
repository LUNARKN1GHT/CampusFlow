# A003 在排期中满足必要任务依赖

作者：李亦凡；任务 #179；父任务 #20。前置 #175（A002）、#157（P005）。

## 已实现

- 排期输入新增依赖边及状态：`planned`、`done`、`cancelled`、`out_of_scope`、`missing`。
- 有向无环依赖先做拓扑排序，再在可执行任务间按截止与优先级排序；同一输入重复生成顺序一致。
- 后续任务的第一个时间块不早于所有前置任务最后一个时间块的结束时间；时间块 `constraint_source` 记录依赖来源。
- 依赖不可满足时任务直接列为未排入：
  - `dependency_unmet`：前置已取消、不存在或不在当前学期范围；前置自身未能排入；
  - `dependency_deadline`：前置完成时间已晚于本任务截止（含提交缓冲）；
  - `dependency_cycle`：任务位于依赖环中；
  - 环的下游任务同样不安排，并说明前置位于环中。
- 依赖状态进入输入快照与指纹：依赖变化后旧草案不能继续接受。

## 验收对照

| 验收条件 | 证据 |
| --- | --- |
| 后续任务不早于必要前置完成 | `test_dependent_task_is_scheduled_after_prerequisite`：后续任务截止更早，但时间块仍排在前置之后，且带 `dependency` 约束来源。 |
| 依赖不可满足的任务列为未排入 | `test_cancelled_prerequisite_leaves_dependent_unscheduled`、`test_dependency_that_finishes_after_deadline_is_unscheduled`、`test_cycle_tasks_are_not_planned` 分别覆盖取消、截止冲突与成环；`test_completed_prerequisite_does_not_delay_dependent` 确认已完成前置不额外延迟。 |

## 验证

```text
backend$ uv run --locked pytest -q tests/test_planning_dependencies.py tests/test_planning_domain.py tests/test_planning_edges.py tests/test_plans_api.py
58 passed
```

真实 PostgreSQL；无新增迁移。

## 未覆盖与限制

- 只把依赖当作"前置全部完成后才能开始"的硬约束，不拆分并行前置。
- 前置部分排入时后续任务不安排，避免把"部分完成"当作已完成。
- 局部重排与锁定时间块保留由 A005 处理；界面由 A008 接入。
