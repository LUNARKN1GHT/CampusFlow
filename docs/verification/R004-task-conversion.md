# R004 已确认任务候选转换为正式任务

任务：[R004 #121](https://github.com/LUNARKN1GHT/CampusFlow/issues/121)，父任务 #12（E31）。作者：李亦凡（LUNARKN1GHT）。日期：2026-10-14。

## 用户决定与实现

用户选择三项：转换作为独立用例且幂等、截止日期解析候选原文（解析不出只留备注）、其余字段尽量映射并保留原文。

`tasks` 新增 `source_candidate_id`（唯一、可空、`ON DELETE SET NULL`），指向来源候选：一个候选最多建一个正式任务，作为幂等依据，也供 R007 的来源详情使用。转换在一个事务里创建任务并写入来源资料与候选关联。

## 字段映射

| 候选字段 | 正式任务 | 说明 |
| --- | --- | --- |
| title | title | 必填；为空或全空白时拒绝转换 |
| submission_method、deliverable | description | 保留原文，按「提交方式：…／产出物：…」分行 |
| due_text | due_date、due_time、due_source、due_note | 见下 |
| estimated_minutes | estimated_minutes、remaining_minutes | 仅接受正整数；其余留空 |
| 来源版本 | source_material_id | 取候选绑定版本的资料 |
| — | source_candidate_id | 幂等键与来源关联 |
| 适用范围 | course_id | 候选课程，须仍属于当前空间 |

## 日期精度与来源

`domain/candidate_conversion.py` 的 `parse_due_text` 只接受代码可解析的写法：

- `YYYY-MM-DD` 只得到日期，**不会**补出 `00:00` 或 `23:59`，正式任务的 `due_time` 保持空。
- `YYYY-MM-DDTHH:MM` 或 `YYYY-MM-DD HH:MM[:SS]` 得到日期与时刻。
- 相对或复杂写法（如「下周五」）、无效日期、带时区偏移的时刻一律解析为「无具体日期」：`due_date` 留空，原文写入 `due_note`，不换算、不猜时点。
- 日期来源按候选字段出处映射：原文规定 → document，系统推断 → inferred，用户补充与规划建议 → user。用户补充的日期不会被写成原文规定。

## 转换入口与规则

应用入口：`application.conversion.convert_candidate_to_task(repos, workspace_id, candidate_id)`。

- 要求候选已确认（复用 R003 的 `require_confirmed_for_conversion`）：待核对、已忽略、已失效的候选不能转换。
- 已完成转换的候选再次调用直接返回既有任务，不新建、不改动既有任务。
- 生效字段取叠加 R002 修正后的取值，因此用户补齐的标题或日期会进入正式任务。
- 转换不修改候选核对状态；任务进度（未开始／进行中／已完成等）与核对状态相互独立。
- 非任务候选（日程、规则）拒绝转换；候选课程已不可用时拒绝并提示重新核对适用范围。

限制：本项不开放 HTTP 接口，日程转换由 R005 负责，规则候选待 X005；不判断解析出的日期语义是否正确，也不做相对日期换算（X006）。

## 验证

`tests/test_candidate_conversion.py`：**23 passed**。使用合成通知、真实 PostgreSQL 测试库与既有候选保存入口，不调用真实模型。覆盖：

- 日期解析保留精度：只给日期不补时刻；带时刻保留；相对写法、无效日期、带时区时刻解析为空。
- 出处到来源类型映射覆盖四种出处。
- 字段映射：标题、提交方式／产出物组合的说明、耗时建议、非任务与缺标题拒绝。
- 转换建立任务并写入 `source_candidate_id`、`source_material_id`，候选仍为已确认。
- 重复转换返回同一任务且任务数不增；数据库唯一约束拒绝为同一候选再建任务。
- 只有已确认候选可转换，待核对与已忽略被拒且不建任务；日程候选被拒。
- 相对截止原文转换后 `due_date` 为空、原文进 `due_note`；用户修正为具体日期后来源为用户且只有日期不补时刻，候选原始原文不变。
- 任务进度改为已完成不影响候选核对状态；候选删除后任务保留且关联置空。

2026-10-14 完整后端回归 **624 passed**，Ruff check 与 `format --check` 通过；`alembic heads` 单一 head，`downgrade -1` 后重升级成功。前端本次无改动。

```powershell
uv run --locked pytest tests/test_candidate_conversion.py -q
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked alembic upgrade head
```

未执行：候选 HTTP 接口联调、核对页面交互、日程转换；前端类型检查与构建留 CI。

## 分支与前置

从父分支 `issue/#12`（已含 R002、R003，`8c9ffb4`）创建 `issue/#121`，本项 PR 的合并目标为 `issue/#12`。前置 R003 #120 已关闭；后续 R005 #122 以同样方式处理日程候选，R007 #124 读取本项写入的来源关联。
