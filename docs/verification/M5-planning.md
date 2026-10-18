# M5 两周计划实现与验收记录

日期：2026-10-18
范围：E50（P001–P010）、E51（P011–P018）的计划后端、页面与自然语言入口。
状态：**实现完成，自动化与浏览器走查通过；MVP 整体验收（V001–V007）仍待独立复验。**

## 分支与合并

| 集成分支 | 叶子分支 | 内容 |
| --- | --- | --- |
| `issue/#15` | `issue/#138` | P001 计划草案、版本与时间块模型 |
| `issue/#15` | `issue/#142` | P002 汇总任务、固定日程、可用时间与偏好 |
| `issue/#15` | `issue/#150` | P003 缺失日期／耗时与显式假设 |
| `issue/#15` | `issue/#152` | P004 可用时间片 |
| `issue/#15` | `issue/#157` | P005 截止约束分配 |
| `issue/#15` | `issue/#162` | P006 优先级、容量与休息 |
| `issue/#15` | `issue/#166` | P007 分段与提交缓冲 |
| `issue/#15` | `issue/#171` | P008 未排任务、缺口与调整选项 |
| `issue/#15` | `issue/#176` | P009 安排理由与约束来源 |
| `issue/#15` | `issue/#181` | P010 边界与不可行输入验证 |
| `issue/#16` | `issue/#158` | P011 时间重叠检测 |
| `issue/#16` | `issue/#177` | P012 截止与负荷风险 |
| `issue/#16` | `issue/#186` | P013 草案生成／版本化接受 API |
| `issue/#16` | `issue/#191` | P014 时间块移动与锁定 |
| `issue/#16` | `issue/#193` | P017 规划与冲突检查自然语言入口 |
| `issue/#16` | `issue/#192` | P015／P016／P018 计划、冲突调整与总览界面 |

叶子分支按父 Issue 分层合并；当前尚无远端 PR，未合并到 `develop`。

## 数据与协议

- 迁移：`backend/migrations/versions/p001_planning_drafts.py`。新增 `plan_drafts`、`plan_blocks`、`plan_versions`。
- 时间块与任务 DDL 分表保存；移动或锁定时间块只写 `plan_blocks`，不改写 `tasks.due_date/due_time`。
- `plan_drafts.version` 每次生成、接受、移动、锁定递增；更新使用 `WHERE version = expected` 原子校验，冲突返回 409。
- 接受时重新采集输入并比较 `input_fingerprint`；任务、固定日程、可用时间或设置变化后，旧草案不能接受。
- 旧草案晚于新接受计划提交接受时返回 409，不覆盖较新计划。
- API：`POST/GET /api/v1/plans/drafts`、`GET /api/v1/plans/drafts/{id}`、`POST /api/v1/plans/drafts/{id}/accept`、`GET /api/v1/plans/current`、`GET /api/v1/plans/drafts/{id}/conflicts`、`PATCH /api/v1/plans/blocks/{id}`、`POST /api/v1/plans/blocks/{id}/lock`、`GET /api/v1/plans/overview`。
- `POST /api/v1/qa/intent` 新增 `execute`；规划意图只生成待接受草案，冲突检查只读取当前计划，均不自动接受或改写安排。

## 验收映射

| 项 | 证据 |
| --- | --- |
| P001 时间块独立于 DDL／稳定版本 | `test_plans_api.py::test_generate_and_accept_plan_without_changing_task_ddl`、`test_move_block_updates_schedule_but_not_task_ddl`、`test_stale_version_and_changed_inputs_are_rejected` |
| P002 时区边界／已完成取消项不待排 | `domain/planning.py::two_week_range`；`test_every_input_task_has_a_destination` |
| P003 假设与待补充分离／候选不自动成为硬约束 | `test_missing_duration_and_due_date_become_gaps_without_assumptions`、`test_user_selected_assumptions_are_applied_and_echoed`；采集只读正式任务 |
| P004 相邻边界／重叠容量不重复 | `test_adjacent_availability_boundaries_do_not_overlap`、`test_overlapping_availability_is_counted_once`、`test_fixed_event_is_subtracted_and_duplicate_events_not_double_counted` |
| P005 不越截止／保留未排原因 | `test_allocation_never_crosses_deadline_and_keeps_shortfall_reason`、`test_passed_deadline_is_not_scheduled` |
| P006 偏好不覆盖硬约束／可解释 | `test_daily_capacity_limits_total_planned_minutes`、`test_break_is_kept_between_blocks`、`test_weekly_capacity_is_respected_across_days` |
| P007 分段总量一致／缓冲 | `test_large_task_is_split_and_total_matches_required_minutes`、`test_submission_buffer_moves_work_earlier_than_deadline` |
| P008 所有任务有去向 | `test_every_input_task_has_a_destination`、`test_capacity_gap_exposes_adjustment_options` |
| P009 理由对应真实约束／估计风险 | `test_block_reason_matches_real_constraints_and_marks_estimates`、`test_confirmed_duration_does_not_claim_estimate_risk` |
| P010 边界与不可行输入 | `tests/test_planning_edges.py`（零可用时间、紧邻 DDL、跨日、超容量、未知耗时、DDL 不变） |
| P011 重叠检测 | `test_overlapping_fixed_events_are_reported`、`test_block_overlapping_fixed_event_is_reported`、`test_overlapping_blocks_are_reported_and_adjacent_blocks_are_not` |
| P012 截止／负荷风险 | `test_deadline_risk_is_marked_as_estimate_based`、`test_confirmed_shortfall_is_a_conflict_not_an_estimate`、`test_daily_and_weekly_load_risks_are_reported` |
| P013 版本化接受 | `test_generate_and_accept_plan_without_changing_task_ddl`、`test_stale_version_and_changed_inputs_are_rejected`、`test_newer_accepted_plan_is_not_overwritten_by_old_draft` |
| P014 移动与锁定 | `test_move_block_updates_schedule_but_not_task_ddl`、`test_move_rejects_overlap_with_fixed_event_and_other_blocks`、`test_lock_and_unlock_accepted_block`、`test_draft_block_cannot_be_moved_before_accepting` |
| P015／P016 页面 | `frontend/src/views/PlanView.vue`；浏览器走查见下 |
| P017 自然语言入口 | `tests/test_qa_api.py::test_planning_and_conflict_intents_execute_without_auto_accepting` |
| P018 今日总览 | `test_overview_reports_today_plan_and_upcoming_tasks`；`frontend/src/views/OverviewView.vue` |

## 自动化检查

```text
backend: uv run ruff check src tests        -> All checks passed!
backend: uv run pytest -q                   -> 1306 passed, 9 warnings
frontend: npm run build                     -> typecheck 通过，vite build 成功
frontend: npx playwright test（清空专用测试库）-> 7 passed, 1 skipped（M2 队列用例按环境跳过）
```

`P010` 的边界测试位于 `backend/tests/test_planning_edges.py`，其余计划领域测试位于 `backend/tests/test_planning_domain.py`。

## 浏览器走查

使用独立端口（后端 8001 + 前端 5174）连接测试库，不改动正在运行的开发服务。登录后在 `/plan` 实际完成：

1. 用 API 在同一会话内创建可用时间与任务；
2. 生成两周草案，页面显示版本、范围、工作空间时区、容量缺口、冲突、未排任务与调整选项；
3. 接受为当前计划，版本 v1 → v2；
4. 锁定时间块，v2 → v3，页面显示“已锁定”；
5. 调整时间到 19:00–19:30，v3 → v4，理由追加“用户手动移动”；
6. 回到 `/` 总览，显示近期截止任务、未完整排入 1、冲突 1。

新增 `frontend/tests/e2e/plan.spec.ts` 固化同一流程：登录 → 设置可用时段 → 创建任务 → 生成草案 → 接受 → 锁定 → 移动 → 总览读取；同时修正旧 e2e 对首页标题、“任务与计划”导航和“待核对”快捷入口的选择器。`frontend/tests/e2e/plan-keyboard.spec.ts` 另以 Tab、Enter 和方向键完成登录、生成、接受、锁定与移动，并验证可见焦点。

走查中发现并修复：

- **时区显示错误**：此前按浏览器时区显示时间块，工作空间为 `America/New_York` 时被显示成第二天凌晨。现在计划接口返回 `timezone`，页面按工作空间时区展示、分组和编辑；移动输入按工作空间墙上时间转成明确时间点。
- **窄屏导航截断**：≤760px 时八个导航项横向溢出，最后一个被裁切。改为换行布局。
- **总览重复空状态**：同一空状态出现两次，已合并。

## 已知限制

- 真实模型语义复核（Q019）仍未补跑；本记录只覆盖确定性排期与协议校验，不代表 MVP 八项验收全部通过。
- 冲突列表在草案生成时保存；接受后手动移动时间块不会重新计算已保存的截止／负荷风险列表（移动会校验固定日程与时间块重叠）。局部重排与风险刷新属于 M7 范围。
- 计划只读取正式任务；未确认候选不参与排期，页面已说明。
- 键盘全流程与语义色对比度已检查；屏幕阅读器（VoiceOver／NVDA）实测与独立无障碍走查仍未执行。
- V001–V007 的 MVP 端到端验收、发布说明和候选版本验收尚未完成。
