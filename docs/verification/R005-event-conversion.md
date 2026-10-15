# R005 已确认日程候选转换为固定日程

任务：[R005 #122](https://github.com/LUNARKN1GHT/CampusFlow/issues/122)，父任务 #12（E31）。作者：李亦凡（LUNARKN1GHT）。日期：2026-10-14。

## 用户决定与实现

用户选择两项：参加条件不落库、来源关联只做候选关联。

`fixed_events` 新增 `source_candidate_id`（唯一、可空、`ON DELETE SET NULL`）：一个候选最多建一个固定日程，作为幂等依据。参加条件仍只在候选详情可见，正式日程不新增列；资料经候选间接关联，本次不改动资料删除的失效语义。

## 字段映射与时间校验

| 候选字段 | 固定日程 | 说明 |
| --- | --- | --- |
| title | title | 必填；为空或全空白时拒绝；超过 300 字符拒绝 |
| start_text | starts_at | 必须能解析出「日期 + 时刻」 |
| end_text | ends_at | 同上；结束必须晚于开始 |
| location | location | 原文可选；超过 200 字符拒绝 |
| 适用范围 | course_id | 候选课程，须仍属于当前空间 |
| — | source_candidate_id | 幂等键 |

`domain/event_conversion.py` 的 `parse_event_time` 只接受带日期与时刻的写法：

- `2026-10-20T14:00`、`2026-10-20 14:00[:SS]` 可解析；`2026-10-20`（只有日期）、`14:00`（只有时刻）、`下周五 14:00`、`2026-10-20 下午两点` 一律解析为空。
- 带 UTC 偏移的原文按时间点保留，随后按工作空间时区换算，不重新解释墙上时间。
- 得到的时间按工作空间时区解释（`in_workspace_timezone`），夏令时缺失或歧义时刻明确报错。

应用入口：`application.conversion.convert_candidate_to_event(repos, workspace_id, candidate_id)`。

- 开始或结束任一不可确定时**拒绝**创建固定日程，不按全天、不按常见时长补造 —— 这就是「缺少必需时间不能成为确定日程」。
- 时间经工作空间时区换算后复用 `validate_fixed_event` 校验：结束必须晚于开始；MVP 固定日程不设重复（`recurrence=none`）。
- 要求候选已确认（复用 R003 守卫）；已转换的候选再次调用返回既有日程。
- 生效字段取叠加 R002 修正后的取值，因此用户补齐的开始与结束时刻会进入正式日程。
- 转换不修改候选核对状态。

限制：本项不开放 HTTP 接口；规则候选待 X005；参加条件不入正式日程；固定日程尚未接入资料删除的失效留痕（D009 目前只覆盖任务），候选随资料删除后日程保留但不再声称来源。

## 验证

`tests/test_candidate_event_conversion.py`：**22 passed**。使用合成通知、真实 PostgreSQL 测试库与既有候选保存入口，不调用真实模型。覆盖：

- 时间解析：日期加时刻可解析；只有日期、只有时刻、相对与口语写法解析为空；带偏移原文保留时间点。
- 字段映射与非日程候选、缺标题、标题／地点超长拒绝；只有日期或缺少一端时拒绝转换。
- 转换建立日程并按工作空间时区换算，`recurrence=none`、无重复截止，写入 `source_candidate_id`，候选仍为已确认。
- 带偏移原文换算为同一时间点，不重新解释墙上时间。
- 重复转换返回同一天程且日程数不增；数据库唯一约束拒绝为同一候选再建日程。
- 相对时间、只有日期、结束早于开始三种情况都拒绝且不建日程。
- 只有已确认候选可转换；任务候选不能转日程、日程候选不能转任务。
- 用户用 R002 补齐开始与结束时刻后可以转换，时间按工作空间时区成立。
- 候选删除后日程保留且关联置空。

2026-10-14 完整后端回归 **646 passed**，Ruff check 与 `format --check` 通过；`alembic heads` 单一 head，`downgrade -1` 后重升级成功。前端本次无改动。

```powershell
uv run --locked pytest tests/test_candidate_event_conversion.py -q
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked alembic upgrade head
```

未执行：候选 HTTP 接口联调、核对页面交互、任务转换（本项只做日程）；前端类型检查与构建留 CI。

## 分支与前置

从父分支 `issue/#12`（已含 R002、R003、R004，`01f83d8`）创建 `issue/#122`，本项 PR 的合并目标为 `issue/#12`。前置 R003 #120 已关闭；后续 R006 #123 保存确认规则，R007 #124 读取本项与 R004 写入的来源关联。
