# N014 版本接受与变更历史

作者：李亦凡；任务 #168；父任务 #19。前置 N012 #160、N013 #164。

## 已实现

`POST /api/v1/candidates/{baseline}/diff/{comparison}/accept` 接受显式选择的字段差异：

- 请求必须列出字段与 1 至 1000 字原因；未知字段或未变化字段直接 400。
- 先复用 N012 判定：`applicable` 可直接接受；`conflict` / `requires_review` 必须显式传 `acknowledge_conflict=true`，否则返回 400 并列出原因。
- 在同一个事务内：旧候选标记为 `superseded` 并写入既有核对状态变更记录，同时追加接受记录；失败整体回滚。
- 接受记录保存旧/新候选与版本、接受的字段、原因、操作者、接受前状态和逐字段左右证据快照（原始值、生效值、出处、引用逐字摘录与定位、可验证状态）。
- 重复提交相同字段与关系的接受返回同一记录（200），不会重复迁移或重复失效。
- `GET /api/v1/candidates/{candidate_id}/acceptances` 返回与该候选相关的全部接受历史，旧版本、原因与证据均可查看。

## 验收对照

| 验收条件 | 证据 |
| --- | --- |
| 接受范围改变后旧确认失效 | `test_accept_supersedes_old_candidate_and_keeps_evidence`：旧候选从 `pending` 变为 `superseded`；`test_confirmed_old_candidate_can_be_superseded`：已确认旧候选在接受后同样失效。 |
| 旧版本、原因与证据均可查看 | 历史接口返回旧/新版本编号、原因、操作者与证据快照；测试断言左右引用分别包含“下周五”“下周三”。重复提交只保留一条记录。 |

## 验证

```text
backend$ uv run --locked pytest tests/test_version_acceptance.py -q
6 passed
```

新增迁移 `n014_version_acceptances`（前置 `m6_merge_n010_p001`）；真实 PostgreSQL。没有模型调用。

## 未覆盖与限制

- 接受记录只更新版本接受状态与候选核对状态，不自动改写已转换任务/时间块；影响范围由 N013 查询，界面对照与执行由 N017 处理。
- 冲突需要用户显式确认，但不会因为确认而修改来源原文或隐藏冲突。
- 接受字段按名称保存；字段值快照保存当时证据，后续原文改写不会改写历史。
