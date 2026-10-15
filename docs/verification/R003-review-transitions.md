# R003 候选核对状态转换

任务：[R003 #120](https://github.com/LUNARKN1GHT/CampusFlow/issues/120)，父任务 #12（E31）。作者：李亦凡（LUNARKN1GHT）。日期：2026-10-14。

## 用户决定与实现

用户选择四项：必需字段按候选类型定义、缺少必需字段时默认拒绝但允许显式确认、状态变更新增记录表留痕、转换集合可撤销。

新增表 `candidate_review_changes`，保存每次核对状态变更的方向、操作者、原因与时间，与既有 `task_progress_changes` 对齐。候选仍只保存当前核对状态，不改动任务执行进度。

## 合法转换

| 当前状态 | 允许转换为 |
| --- | --- |
| 待核对 pending | 已确认 confirmed、已忽略 ignored、已失效 superseded |
| 已确认 confirmed | 待核对 pending（重新核对）、已失效 superseded |
| 已忽略 ignored | 待核对 pending（撤销忽略） |
| 已失效 superseded | 待核对 pending（重新打开） |

同一状态不重复转换，也不允许已忽略直接转为已确认或已失效；其余组合返回领域错误。已失效只允许重新打开，不再直接跳转，避免把失效项当作重新确认。

## 必需字段与确认检查

必需字段按候选类型定义，与提取 spec 的 `allow_missing=False` 保持一致（测试交叉核对两处不漂移）：

| 类型 | 必需字段 |
| --- | --- |
| 任务 task | title |
| 日程 event | title、activity_text |
| 规则 rule | （X005 未开工，暂不设必需字段） |

应用入口：`application.candidates.set_candidate_review_status(repos, workspace_id, candidate_id, status, *, operator, reason=None, acknowledge_missing=False)`。按空间授权读取候选，校验转换合法后更新状态并追加留痕；失败整条回滚。

- 确认为已确认前检查必需字段，检查的是叠加 R002 修正后的生效取值，因此用户已补充的字段视为已具备。
- 缺少必需字段时默认拒绝，并列出仍然缺失的字段名；用户确需确认时必须显式传 `acknowledge_missing=True` 且填写原因，否则拒绝。这就是「缺少必须字段不能被无提示确认」。
- `require_confirmed_for_conversion` 供 R004、R005 在转换为正式事项前调用：被忽略或已失效的候选不参与正式约束。

限制：本项不开放候选 HTTP 接口，不做正式任务或日程生成（R004/R005）、重复事项合并（N015/N018）与核对界面（R008/R009）；不判断确认后的业务正确性。

## 验证

`tests/test_candidate_review.py`：**24 passed**。使用合成通知、真实 PostgreSQL 测试库与既有候选保存入口，不调用真实模型。覆盖：

- 必需字段表与 TASK/EVENT 提取 spec 交叉核对一致；只有类型必需的缺失字段才计入。
- 合法转换全部接受，非法转换（含已忽略/已失效直接转已确认）与同状态转换被拒绝；空白操作者被拒绝。
- 确认写入状态与留痕，前后状态、操作者与原因可回读；缺必需字段被拒绝且不写库。
- 显式确认缺必需字段必须填写原因，填写后确认成功并记录原因。
- 必需字段由 R002 用户修正补齐后可以确认（生效取值参与检查）。
- 忽略 → 重新打开 → 确认的完整流程按序留痕；失败转换不改变状态与历史。
- 跨空间访问返回未找到；数据库约束拒绝非法状态；候选删除时留痕随之清理。

2026-10-14 完整后端回归 **601 passed**，Ruff check 与 `format --check` 通过；`alembic heads` 单一 head，`downgrade -1` 后重升级成功。前端本次无改动。

```powershell
uv run --locked pytest tests/test_candidate_review.py -q
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked alembic upgrade head
```

未执行：候选 HTTP 接口联调、核对页面交互、正式事项生成；前端类型检查与构建留 CI。

## 分支与前置

从父分支 `issue/#12`（已含 R002，`79c2ca4`）创建 `issue/#120`，本项 PR 的合并目标为 `issue/#12`。前置 R002 #119 已关闭；后续 R004 #121、R005 #122 在本项之上实现正式事项转换。
