# A012 验证提醒幂等与重复调度保护

作者：李亦凡；任务 #195；父任务 #21。前置 #187（A011）、J004。

## 幂等键

`backend/src/campusflow/domain/reminders.py` 集中生成三类键：

- 截止提醒：`deadline:{task_id}:{rule_id|default}:{rule_version}:{due_date}:{due_time|date}:{window_timestamp}`
- 任务来源失效：`review-task:{task_id}:{due_date}:{rule_version}`
- 工作空间待核对：`review:pending:{day}`

规则更新会递增版本，从而产生新的键；同一版本、同一触发窗口在重试和重复调度周期中保持同一键。键最长 200 字符，超长时保留前缀并附加内容哈希，避免简单截断碰撞。

## 保护措施

1. **唯一约束**：`reminder_triggers.dedupe_key` 唯一；写入使用 `INSERT … ON CONFLICT`，不因重复调度报错。
2. **批量去重**：同步前按 `dedupe_key` 合并同一批期望记录，避免一次插入重复行。
3. **状态保护**：冲突更新只把 `cancelled` 恢复为 `pending`；已读（`sent` 且有 `read_at`）与已忽略（`dismissed`）不会被重新激活。
4. **失败恢复**：同步在事务内执行，任一步失败整体回滚；重试会重新计算期望集合并幂等写入。
5. **并发写入**：两个独立会话同时写入同一键，最终只保留一行。

## 验证

```text
backend$ uv run --locked ruff check src tests
All checks passed!
backend$ uv run --locked pytest -q tests/test_reminder_idempotency.py tests/test_reminders.py tests/test_reminder_generation.py
17 passed
```

真实 PostgreSQL；无新增迁移。并发用例使用两个独立数据库会话写同一 `dedupe_key`。

## 未覆盖与限制

- 当前没有后台调度进程，幂等保护作用于读取时同步；接入定时任务后沿用同一仓储写入即可。
- 并发用例验证数据库层唯一约束，不模拟应用进程崩溃后的队列重投；队列投递幂等仍以 J004 为准。
