# R002 候选字段修正记录

任务：[R002 #119](https://github.com/LUNARKN1GHT/CampusFlow/issues/119)，父任务 #12（E31）。作者：李亦凡（LUNARKN1GHT）。日期：2026-10-14。

## 用户决定与实现

用户选择三项：修正按追加保存、来源类型复用 X001 的 FieldOrigin、本项只做应用层不开 HTTP 接口。

新增表 `candidate_field_revisions`，一条记录保存一次字段修正：修正前后取值与来源类型、操作者和可选说明。`extracted_fields` 与 `candidate_field_evidence` 保持原样，因此原始提取与证据始终可取回；当前生效值由原始字段按时间顺序叠加修正得到。

| 字段 | 含义与约束 |
| --- | --- |
| previous_value / new_value | 修正前后的取值，可为 JSON null；均以 JSONB 保存 |
| previous_origin / new_origin | 修正前后的来源类型，取值同 FieldOrigin |
| operator | 操作者，必填，最长 100 字符 |
| note | 可选说明；修正为空值时必须给出缺失原因，标为规划建议时必须给出依据 |

## 应用入口与规则

应用入口：`application.candidates.revise_candidate_field(repos, workspace_id, candidate_id, field_name, *, value, origin, operator, note=None)`。按空间授权读取候选，取该字段当前生效值构造修正，校验通过后追加一条记录并返回叠加后的候选；跨空间或候选不存在返回未找到，字段名不存在返回领域错误。

- 逐字段沿用 X001 的字段出处规则：原文规定与系统推断必须有来源证据，推断与建议必须有说明，空值必须有缺失原因。
- 修正值不继承原文证据，因此用户直接填写的日期不能声明为原文规定；来源只能是用户补充或规划建议，其余合法但缺依据的声明被拒绝。这正是「用户填写的日期不会伪装成原文规定」。
- `previous_origin` 与 `previous_value` 必须与字段当前状态一致，避免用过期取值伪造修正起点。
- 同一字段多次修正按先后顺序追加，历史不被覆盖；`effective_fields` 供界面展示生效取值。
- 候选核对状态不随修正改变，仍为 pending；确认、忽略与失效转换由 R003 定义。

限制：本项不开放候选 HTTP 接口，也重复合并与正式事项生成不在范围内；不调用真实模型，也不判断修正后的取值语义正确。

## 验证

`tests/test_candidate_revisions.py`：**15 passed**。使用合成通知、真实 PostgreSQL 测试库和既有候选保存入口，不调用真实模型。覆盖：

- 用户填写的日期声明为原文规定时被拒绝；标为用户补充时接受，生效值改为修正值且不再挂原文证据，原始字段与证据不变。
- 修正前取值或来源与当前状态不一致、空值缺原因、建议缺说明、操作为空时被拒绝。
- 追加往返：修正前后值、来源与操作者写库后可回读，候选仍 pending，原始三字段与两条证据仍在库中。
- 连续两次修正按顺序追加，第二次的修正前值等于第一次的修正后值，生效值取最后一次。
- 字段不存在、跨空间访问被拒绝且不写入；伪装成原文规定的修正不产生任何记录；提交失败整条回滚。
- 数据库约束拒绝非法来源；候选删除时修正记录随之清理。

2026-10-14 完整后端回归 **577 passed**，Ruff check 与 `format --check` 通过；`alembic heads` 单一 head，`downgrade -1` 后重升级成功。前端本次无改动。

```powershell
uv run --locked pytest tests/test_candidate_revisions.py -q
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked alembic upgrade head
```

未执行：真实模型调用、候选 HTTP 接口联调、核对页面交互；前端类型检查与构建未在本机重跑（无前端改动，留 CI）。

## 分支与前置

从当时最新 `develop`（`c26e9df`，E30 X001–X004 先行合并后）创建父分支 `issue/#12`，再创建 `issue/#119`，本项 PR 的合并目标为 `issue/#12`。前置 X001 #108 已关闭；后续 R003 #120 在本项之上实现状态转换。
