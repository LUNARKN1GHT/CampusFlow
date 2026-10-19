# A004 可编辑子任务与耗时汇总

作者：李亦凡；任务 #180；父任务 #20。前置 #170（A001）、#64（T009）。

## 已实现

- `POST /api/v1/tasks/{id}/split-suggestions` 只返回可编辑拆分建议，不写库；建议固定带 `suggestion=true`、`editable=true`，按父任务剩余耗时平均分配。
- `POST /api/v1/tasks/{id}/subtasks` 批量创建子任务，继承父任务课程与父任务关联；子任务可继续用普通任务接口调整标题、耗时、截止与进度。
- `GET /api/v1/tasks/{id}/subtasks`、`GET /api/v1/tasks/{id}/rollup` 与关系接口返回父任务汇总：子任务数、预计/剩余耗时、缺失耗时数量、进度分布与完成百分比。
- 汇总只读，不改写父任务自身的预计或剩余耗时。
- 排期把"有直接子任务的任务"当作容器：父任务列入 `excluded`（`container_with_subtasks`），只排子任务，避免父子耗时重复计入。
- 仅支持一层子任务；创建孙任务或把已有子任务的任务设为子任务时拒绝。

## 验收对照

| 验收条件 | 证据 |
| --- | --- |
| 系统拆分标为建议 | `test_split_suggestion_is_editable_and_not_persisted`：建议带 `suggestion=true`，且子任务列表仍为空。 |
| 父子耗时不会在排期中重复计入 | `test_parent_minutes_are_not_double_counted_in_schedule`：父任务 120 分钟 + 两个各 30 分钟子任务，草案总时长 60 分钟，父任务以 `container_with_subtasks` 排除。 |
| 父任务进度与耗时汇总 | `test_create_edit_and_roll_up_subtasks`：调整子任务剩余耗时、完成后汇总与百分比更新，父任务自身字段不变。 |

## 验证

```text
backend$ uv run --locked pytest -q tests/test_subtasks.py tests/test_task_dependencies.py tests/test_planning_dependencies.py
14 passed
```

真实 PostgreSQL；无新增迁移。

## 未覆盖与限制

- 拆分建议是固定模板与平均分配，不调用模型；用户需编辑后再创建。
- 仅支持一层子任务；嵌套分解留后续评估。
- 界面调整与排期展示由 A008 接入。
