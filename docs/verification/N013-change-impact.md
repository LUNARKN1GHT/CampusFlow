# N013 通知变更影响的任务与时间块

作者：李亦凡；任务 #164；父任务 #19。前置 N011、R007、P001。

## 已实现

`GET /api/v1/candidates/{baseline}/diff/{comparison}/impact` 在 N011 差异之上只读列出旧候选已经转换出的正式对象：

- 任务：标题、执行进度、是否完成、日期精度、来源状态，以及关联的计划时间块。
- 时间块：所属草案、草案状态、起止时间、是否锁定；已完成任务与锁定安排照常列出，不被过滤。
- 固定日程与课程规则：按来源候选关联读取。
- 未接入能力：响应明确返回 `reminders` 与 `external_calendar` 两项 `unsupported`，说明本次没有调整提醒或写入外部日历，不能声称已同步。

接口不执行变更、不生成调整草案、不修改任务或时间块。

## 验收对照

| 验收条件 | 证据 |
| --- | --- |
| 已完成和锁定安排也明确列出影响 | `test_completed_task_and_locked_block_are_listed_with_unsupported_boundaries`：任务进度改为 `done`、时间块 `locked=true` 后仍出现在结果与汇总中。 |
| 当前尚无的提醒或外部连接不伪装已同步 | 响应固定包含 `reminders`、`external_calendar` 的 `unsupported` 说明；测试断言两项都存在。 |

## 验证

```text
backend$ uv run --locked pytest tests/test_change_impact.py -q
2 passed
```

真实 PostgreSQL；无新增迁移、依赖或模型调用。第二个用例验证没有正式事项时返回空列表而不是伪造影响。

## 未覆盖与限制

- 尚未根据差异生成调整草案或执行局部重排；本任务只报告影响。
- 计划时间块按任务关联读取；没有任务关联的缓冲块不列入。
- 提醒和外部日历尚未实现，因此明确标为未接入。
