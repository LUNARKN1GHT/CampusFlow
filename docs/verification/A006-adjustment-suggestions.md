# A006 根据变化生成调整建议

作者：李亦凡；任务 #203；父任务 #20。前置 #199（A005）、#168（N014）、#64（T009）、#51（W007）。

## 已实现

- `GET /api/v1/plans/adjustments` 比较当前排期输入与已接受计划的输入指纹，只读返回：
  - `has_changes` 与逐条 `suggestions`（任务新增、截止/耗时/进度/优先级变化、可用时间、容量偏好、固定日程、依赖、是否参与排期）；
  - `affected_task_ids`；可用时间、容量或固定日程变化时 `affected_all_tasks=true`；
  - `auto_applied=false`、`requires_confirmation=true`，并返回当前计划编号与版本。
- 没有已接受计划时返回明确说明，不比较、不生成。
- `POST /api/v1/plans/adjustments/draft` 只在用户显式请求后生成调整草案；无变化时返回 409。
- 新草案仍为 `draft` 状态：接受前当前计划、版本和时间块都不变；接受后也只更新时间块，不改任务原始 DDL。

## 验收对照

| 验收条件 | 证据 |
| --- | --- |
| 新通知接受后只提出调整 | 通知变更落到任务字段后，`test_progress_change_only_suggests_and_never_overwrites_plan` 断言只返回建议且当前计划版本不变、草案数量不变。 |
| 不会未经接受覆盖当前计划 | 同一用例在生成调整草案后再次断言当前计划仍是旧计划；接受后才切换，任务截止保持 `2026-10-20`。 |
| 变化范围可定位 | `test_availability_change_marks_all_tasks_affected` 标记全部任务；`test_new_task_is_suggested_for_planning` 只标记新增任务。 |

## 验证

```text
backend$ uv run --locked pytest -q tests/test_planning_adjustments.py
4 passed
```

真实 PostgreSQL；无新增迁移。

## 未覆盖与限制

- 变化检测比较排期输入快照；通知原文差异与受影响事项由 N013 提供，本任务不重复解析通知。
- 不自动生成草案，也不自动接受；界面由 A008 接入。
- 调整草案沿用 A005 的重排范围选择，不做逐任务最优移动。
