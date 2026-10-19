# A001 任务依赖与子任务模型

作者：李亦凡；任务 #170；父任务 #20。

## 已实现

- `tasks.parent_task_id` 保存包含关系；`task_dependencies` 单独保存"先完成前置任务"的先后依赖，两套关系不互相推算。
- 创建任务时可指定 `parent_task_id`；未选课程时子任务继承父任务课程，但不复制父任务的来源资料或候选关联。
- `GET /api/v1/tasks/{id}/relations` 返回父任务、子任务、前置任务和后续任务。
- `PATCH /api/v1/tasks/{id}/parent` 设置或清除父任务；父链出现自己时拒绝，避免包含关系成环。
- `POST /api/v1/tasks/{id}/dependencies` 添加依赖，重复添加幂等；`DELETE` 删除依赖。
- 所有关系操作校验任务属于当前工作空间；依赖两端必须存在，且不能依赖自己。

## 验收对照

| 验收条件 | 证据 |
| --- | --- |
| 依赖与包含关系分开 | 独立列与独立表；`test_subtask_keeps_parent_without_creating_dependency` 断言设置父任务后 `task_dependencies` 行数为 0，`prerequisites` 为空。 |
| 子任务保留父任务与来源关联 | `test_subtask_keeps_its_own_source_association` 直接写入来源资料后设置父任务，断言 `parent_task_id` 与 `source_material_id` 同时保留，接口 `source_state=available`。 |
| 关系归属当前空间 | 应用层复用 `_require_task_in_workspace`；跨空间 ID 按"任务不存在"处理。 |
| 包含关系不成环 | `test_parent_chain_cannot_form_cycle` 返回 400 并说明成环。 |

## 验证

```text
backend$ uv run --locked pytest -q tests/test_task_dependencies.py tests/test_tasks_api.py
21 passed
```

真实 PostgreSQL；新增迁移 `a001_task_dependencies`。

## 未覆盖与限制

- 循环依赖与缺失前置的图级校验属于 A002；本任务只阻止自依赖。
- 子任务耗时的可编辑汇总与排期参与属于 A004。
- 未接入前端；依赖与子任务界面由 A008 完成。
