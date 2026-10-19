# N012 版本适用性与待核对冲突

作者：李亦凡；任务 #160；父任务 #19。前置 N011 #155。

## 已实现

`GET /api/v1/candidates/{baseline}/diff/{comparison}/applicability` 在 N011 字段差异之上只读判定：

- 没有明确的 `replacement_claim`（新版本声明替代旧版本）时不按导入时间判定，返回 `requires_review`。
- 替代声明方向相反时返回 `conflict`。
- 范围逐项比较：任一已知字段不同返回 `conflict`；缺少范围信息返回 `requires_review`。
- 发布时间：新版本早于旧版本返回 `conflict`；任一版本缺完整发布时间时不能独立判新生效，返回 `requires_review`。
- 来源归档时要求先恢复。
- 只有“新→旧替代声明 + 范围相同 + 双方发布时间完整且新不早于旧 + 均未归档”才返回 `applicable`，推荐新候选。
- 任何情况都 `applied=false`，不写库、不改候选状态、不改正式事项；接受与历史由 N014 处理。

响应同时带回 N011 的完整 `diff`、双方发布信息、关联记录编号和中文原因代码。

## 验收对照

| 验收条件 | 证据 |
| --- | --- |
| 较晚导入的旧通知不能自动覆盖 | `test_later_import_of_older_notice_never_overrides`：发布时间更早的新版本即使有替代声明也返回 `conflict`；`test_missing_replacement_declaration_requires_review_without_writes`：没有声明时进入待核对且两个候选仍为 `pending`。 |
| 证据不足进入待核对 | 范围缺失、发布时间缺失、来源归档均返回 `requires_review` 并给出原因代码；不自动推荐候选。 |

## 验证

```text
backend$ uv run --locked pytest tests/test_version_applicability.py -q
4 passed
```

未新增迁移、依赖或模型调用。真实 PostgreSQL、登录与空间隔离沿用现有测试夹具。

## 未覆盖与限制

- 只判定候选字段层面的版本适用性，不处理正式任务/时间块影响（N013）与接受历史（N014）。
- 发布时间只使用资料/版本已保存的完整时间；日期原文不换算、不补时刻。
- 不做自动配对，调用方必须显式给出基准与新候选。
