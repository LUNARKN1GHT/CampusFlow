# A007 今天、本周与自定义规划区间

作者：李亦凡；任务 #204；父任务 #20。前置 #199（A005）。

## 已实现

- 创建草案支持 `range_type`：
  - `today`：工作空间时区的当天；
  - `this_week`：当天所在周的周一至周日，可跨月；
  - `two_weeks`：今天至第 14 天；
  - `custom`：必须同时给出 `start_date` 与 `end_date`，允许单日区间。
- 未传 `range_type` 时保持兼容：带日期视为 `custom`，否则为 `two_weeks`；预设区间再传自定义日期会 400。
- 区间边界全部按工作空间时区解释；结束早于开始时拒绝。
- 新草案不接触当前已接受计划；局部重排的子区间会保留区间外现有时间块，避免接受后丢失区间外安排。
- 空区间/无可用时间时返回空时间块与逐任务未排原因，不伪造安排。

## 验收对照

| 验收条件 | 证据 |
| --- | --- |
| 区间外当前安排不被覆盖 | `test_new_range_draft_does_not_touch_current_plan` 断言生成今日草案后当前计划仍为原计划；`test_reschedule_subrange_keeps_blocks_outside_range` 断言区间外时间块原样保留且只出现一次，新草案可接受。 |
| 跨周跨月及空范围有清晰结果 | `test_this_week_range_crosses_month_boundary` 覆盖 09-28 至 10-04；`test_this_week_range_uses_monday_to_sunday` 核对接口边界；`test_today_range_without_availability_returns_clear_empty_result` 返回空块与 `no_available_time`。 |
| 自定义区间校验 | `test_custom_range_requires_both_dates` 覆盖缺结束日期与预设区间带自定义日期。 |

## 验证

```text
backend$ uv run --locked pytest -q tests/test_planning_ranges.py tests/test_plans_api.py tests/test_planning_reschedule.py tests/test_planning_adjustments.py tests/test_planning_dependencies.py
32 passed
```

真实 PostgreSQL；无新增迁移。

## 未覆盖与限制

- 区间类型只影响生成与重排范围，不改变固定日程、任务 DDL 或现有接受计划。
- 跨时区用户的"本周"以工作空间时区为准，不按浏览器本地时区。
- 规划区间界面由 A008 接入。
