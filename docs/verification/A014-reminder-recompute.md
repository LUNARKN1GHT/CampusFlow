# A014 验证计划与通知变化后的提醒重算

作者：李亦凡；任务 #208；父任务 #21。前置 #200（A013）、A006、N014。

## 覆盖场景

1. **未接受草案**：生成计划草案不会改变提醒；同一任务的触发记录 id 与状态保持不变。
2. **接受计划**：接受草案后重算提醒，但提醒不反向修改任务截止或计划内容。
3. **通知变更**：任务截止按已接受的新要求更新后，旧窗口撤消，只保留与新截止匹配的待发送记录，不同时存在两套。
4. **接受变更入口**：N014 `accept_change` 在提交前执行提醒重算，旧触发不会跨事务残留；未接受差异不会触发。

## 实现

- `application/planning.py::accept_draft` 在标记接受后、提交前调用 `sync_reminders`。
- `application/version_acceptance.py::accept_change` 在写入接受记录后、提交前调用 `sync_reminders`。
- 两处都在同一事务内；失败会回滚接受与提醒，避免“已接受但提醒仍旧”。

## 验证

```text
backend$ uv run --locked ruff check .
All checks passed!
backend$ uv run --locked pytest -q tests/test_reminder_recompute.py
3 passed
```

真实 PostgreSQL；无新增迁移。相关回归：`test_reminder_lifecycle.py`、`test_reminder_idempotency.py`。

## 未覆盖与限制

- N014 接受记录本身不改写任务字段；提醒重算以任务当前生效字段为准。新版本字段何时落到任务仍由 R009／通知变更流程决定。
- 计划内容不参与当前截止提醒计算；重算仅保证提醒与任务最新字段一致。
