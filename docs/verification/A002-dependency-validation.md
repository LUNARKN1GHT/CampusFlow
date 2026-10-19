# A002 校验任务依赖成环和缺失前置

作者：李亦凡；任务 #175；父任务 #20。前置 #170（A001）。

## 已实现

- 新增依赖时在候选边上做全空间环检测；成环直接 400，并在错误中给出 `任务 → 任务 → 任务` 的具体路径。
- `GET /api/v1/tasks/dependencies/check?semester_id=` 只读返回：
  - `cycles`：含环强连通分量及任务标题；
  - `missing_prerequisites`：前置任务不在当前学期范围（`prerequisite_out_of_scope`）或已不存在（`prerequisite_missing`）；
  - `cancelled_prerequisites`：前置任务执行进度为已取消；
  - `blocked_tasks`：受影响任务及逐条阻塞原因。
- 成环检查覆盖整个工作空间；缺失与取消按所选任务范围判断。
- 接口不修改任务、依赖或核对状态。

## 验收对照

| 验收条件 | 证据 |
| --- | --- |
| 成环不能被当作可执行顺序 | `test_new_dependency_cycle_is_rejected_with_path`：第三条边返回 400 且不落库；`test_check_reports_cycle_and_blocks_tasks`：历史环被识别，两个任务都在 `blocked_tasks` 且原因为 `dependency_cycle`。 |
| 缺失或取消前置展示阻塞原因 | `test_cancelled_prerequisite_blocks_dependent` 断言 `prerequisite_cancelled` 与中文原因；`test_prerequisite_outside_selected_semester_is_missing` 断言跨学期前置为 `prerequisite_out_of_scope`，不选学期时依赖可满足。 |

## 验证

```text
backend$ uv run --locked pytest -q tests/test_task_dependency_check.py tests/test_task_dependencies.py
9 passed
```

真实 PostgreSQL；无新增迁移。

## 未覆盖与限制

- 目前没有删除正式任务的接口；删除前置导致边被级联清理的情况不会留下记录。若后续开放任务删除，应先展示受影响依赖再删除。
- 阻塞检查只用于展示与后续排期，不自动修改任务进度或依赖。
- 依赖参与排期由 A003 实现，界面由 A008 接入。
