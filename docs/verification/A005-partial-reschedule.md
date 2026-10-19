# A005 保留锁定时间块的局部重排

作者：李亦凡；任务 #199；父任务 #20。前置 #179（A003）、#191（P014）。

## 已实现

- `POST /api/v1/plans/reschedule` 基于当前已接受计划生成新的待接受草案：
  - 锁定时间块始终原样保留（起止、任务、标题、`locked=true`）；
  - 指定 `task_ids` 时，范围外任务的现有时间块也原样保留；
  - 未锁定的范围内任务重新分配，保留块占用可用时间，对应任务只排剩余分钟；
  - 范围默认为当前计划范围，可显式覆盖。
- 保留块进入输入快照与指纹，接受新草案时按同一组保留块重新核对；输入变化仍会拒绝。
- 保留块与任务剩余耗时去重：任务被保留块完全覆盖时不再生成新时间块，`coverage.tasks_scheduled` 仍计入该任务。
- 局部重排结果仍需用户显式接受；未接受前当前计划不变。

## 验收对照

| 验收条件 | 证据 |
| --- | --- |
| 保留锁定时间块 | `test_reschedule_preserves_locked_block`：锁定块起止不变、`locked=true`，总时长仍为两个任务的 120 分钟，新草案可接受。 |
| 范围外安排不移动 | `test_reschedule_outside_scope_keeps_other_task_blocks`：只重排指定任务，另一任务时间块原样保留（未锁定）且可接受。 |
| 不重复安排已保留耗时 | `test_fully_locked_task_is_not_scheduled_twice`：全部时长被锁定块覆盖时只有 1 个 60 分钟块，无未排项。 |
| 必须有已接受计划 | `test_reschedule_requires_accepted_plan` 返回 404。 |

## 验证

```text
backend$ uv run --locked pytest -q tests/test_planning_reschedule.py tests/test_plans_api.py tests/test_planning_domain.py tests/test_planning_edges.py tests/test_planning_dependencies.py tests/test_subtasks.py
66 passed
```

真实 PostgreSQL；无新增迁移。

## 未覆盖与限制

- 局部重排仍是整体重新分配范围内任务，不做单块最优移动或迭代优化。
- 调整建议与触发来源由 A006 实现；界面由 A008 接入。
- 保留块若与新固定日程重叠会作为冲突返回，但不会自动移动保留块。
