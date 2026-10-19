# A013 验证完成、取消、暂停与重新打开

作者：李亦凡；任务 #200；父任务 #21。前置 #195（A012）、T006。

## 覆盖场景

1. **完成／取消**：任务改为 `done` 或 `cancelled` 时，待发送提醒立即撤消；已发送历史保留。
2. **重新打开**：改回 `in_progress` 时，仍有效的已取消提醒恢复为待发送，沿用原记录，不新建重复行。
3. **已发送不重发**：已读或已发送的提醒在完成重开后保持原状态，不会再次弹出。
4. **规则暂停／恢复**：暂停后待发送提醒撤消；恢复后按新规则版本重新生成，仅保留一条待发送。
5. **规则停用**：任务规则 `enabled=false` 时不生成提醒，原待发送记录被撤消。
6. **截止变化**：修改任务截止后，旧窗口撤消、只保留与新截止匹配的待发送提醒。

## 实现

- `create_task`、`update_task`、`set_progress` 在提交前调用 `sync_reminders`（按任务范围），使进度变化即时反映到提醒，而不是等到下次读取。
- 同步本身仍在同一事务内；提醒失败会让任务写入回滚，避免出现“任务已改、提醒仍旧”的半完成状态。

## 验证

```text
backend$ uv run --locked ruff check src tests
All checks passed!
backend$ uv run --locked pytest -q tests/test_reminder_lifecycle.py
5 passed
```

真实 PostgreSQL；无新增迁移。相关回归见 `test_reminders.py`、`test_reminder_generation.py`、`test_reminder_idempotency.py`。

## 未覆盖与限制

- “暂停”只指提醒规则暂停，不是任务执行状态；任务没有 paused 进度。
- 已完成任务不会重新生成未来提醒；重新打开后按当前时间重新计算，已经过去的窗口不会补发。
