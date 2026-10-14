# R007 任务与日程的来源详情

任务：[R007 #124](https://github.com/LUNARKN1GHT/CampusFlow/issues/124)，父任务 #12（E31）。作者：李亦凡（LUNARKN1GHT）。日期：2026-10-14。

## 用户决定与实现

用户选择两项：接口做成受保护的 HTTP 只读子资源、资料删除后的失效留痕从任务扩展到固定日程。

## 接口

| 接口 | 说明 |
| --- | --- |
| `GET /api/v1/tasks/{task_id}/source` | 任务的来源详情 |
| `GET /api/v1/fixed-events/{event_id}/source` | 固定日程的来源详情 |

两者都走本机会话与工作空间授权，跨空间访问与不存在同样返回 404（W006 口径）；与本任务无关的对象不返回。

返回结构（`SourceDetailOut`）：

| 字段 | 含义 |
| --- | --- |
| object_type / object_id | task 或 fixed_event 及其编号 |
| source_type | extracted（提取而来）或 manual（手工创建） |
| source_state | available / invalidated / none |
| source_note | 来源已失效时的说明（含原资料标题） |
| material_id / material_title / version_no | 来源资料与版本（无来源时为 null） |
| candidate_id | 来源候选（失效或手工时为 null） |
| due_source / due_note | 仅任务：正式截止的来源类型与备注 |
| fields | 候选逐字段：名称、值、出处、说明与引用 |
| corrections | 用户逐字段修正记录（R002） |

- `source_type` 只按来源关联判定，不看字段内容：有来源候选或来源资料、或留过失效痕迹即为 extracted，否则 manual。
- `fields` 返回候选**原始**提取（叠加修正前的取值），因此「原文要求」（origin=document）与「用户补充」（origin=user）在同一列表里按出处分开，不会被改写。
- 每条引用给出片段编号、逐字摘录与 `locator_type` / `locator_value` 定位；`verifiable` 表示该字段的全部引用当前是否仍能在原文找到。
- 无法定位的引用不会作为可验证引用返回：摘录从引用列表中移除，字段标记 `verifiable=false`，但值仍如实展示，便于用户判断。

## 资料删除后的行为

- 任务：删除资料时按既有 D009 流程解除关联并写下失效时间与说明；候选随资料级联删除后 `source_candidate_id` 置空。来源详情返回 `source_state=invalidated`、`source_type=extracted`、说明非空、`fields` 与 `corrections` 为空——不再返回任何可验证引用。
- 固定日程：本项把同样的失效留痕扩展到日程。`fixed_events` 新增 `source_invalidated_at` 与 `source_invalidated_note`（迁移 `r006_event_source_invalidation`），删除资料前按候选集合解除日程关联并写入痕迹。日程始终保留（删除接口只有 `keep_tasks` 选项），因此不会静默消失，也不会继续声称引用可验证。
- 删除影响预览新增 `linked_fixed_event_ids`，并计入删除确认指纹：关联日程集合变化时旧确认失效。资料页面同时显示「关联日程将标记来源失效」的数量。

## 验证

`tests/test_source_detail.py`：**8 passed**。使用合成通知、真实 PostgreSQL 测试库与既有候选保存入口，不调用真实模型。覆盖：

- 提取任务：来源类型为 extracted、状态 available，返回来源资料/版本与候选编号，逐字段出处正确，document 字段带可定位引用且 `verifiable=true`，suggestion 字段带说明。
- 用户修正出现在 `corrections`（修正前后值、来源、操作者），候选原始字段与证据保持不变。
- 手工任务与手工日程：source_type=manual、状态 none、无字段与引用。
- 日程来源详情与任务同构；跨空间与不存在对象返回 404。
- 删除资料后（保留任务）：任务与日程都返回 `invalidated`、说明非空、字段与引用为空，日程仍然存在；预览中的关联日程编号在转换后出现并使确认指纹变化。
- 引用无法定位时（片段被直接删除）该字段不再返回引用并标记 `verifiable=false`。

2026-10-14 完整后端回归 **654 passed**，Ruff check 与 `format --check` 通过，前端 `npm run build`（含类型检查）通过；`alembic heads` 单一 head，`downgrade -1` 后重升级成功。

```powershell
uv run --locked pytest tests/test_source_detail.py -q
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked alembic upgrade head
cd ../frontend; npm run build
```

未执行：浏览器端联调（本轮只加了删除预览的数量提示，未新增读取来源详情的界面，交由 R008/R009）；未调用真实模型。

## 分支与前置

从父分支 `issue/#12`（已含 R002–R005，`4d45ebd4`）创建 `issue/#124`，本项 PR 的合并目标为 `issue/#12`。前置 R004 #121、R005 #122、D009 #92 均已关闭；后续 R009 #126 在核对界面中展示修改来源并链接正式事项。
