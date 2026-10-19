# C010–C014 课程图分析、顺序与学期辅助

## 交付内容

- `GET /api/v1/curriculum-plans/{id}/order`：只对强制先修分层；输出 `levels`、`alternatives`、`problems` 与 `limitations`。
- `GET /api/v1/curriculum-plans/{id}/next`：按已修／在修状态返回每门课的 `available`、`unmet`（硬条件）与 `recommended`（建议先学，不阻塞）。
- `POST /api/v1/curriculum-plans/{id}/semester-plan`：按已知开课季节与学分上限给出学期建议，返回 `terms`、`unplaced`、`limitations`、`assumptions`，并固定 `graduation_audit=false`。
- 分析统一复用 `analyze_plan`：存在环、矛盾、缺项或未转换候选时，顺序与学期建议保持阻塞，不输出伪造的完整结论。

## 验收对应

| 条件 | 实现与证据 |
| --- | --- |
| C010 自环与多节点环可定位 | `/order` 的 `problems` 带 `path` 与 `relation_ids`；`test_cycle_and_self_loop_block_order_with_concrete_path` 覆盖 A→B→C→A 与 D→D |
| C010 成环不返回完整顺序 | 成环时 `levels=[]`、`blocked=true`；同一测试断言 |
| C011 缺项展示节点或证据 | 无法匹配的关系候选进入 `missing`，带 `candidate_ids`；`test_unmatched_relation_candidate_stays_missing_and_blocks_completeness` |
| C011 未核对前不宣称图完整 | `complete=false`，`/order` 同样阻塞；同一测试 |
| C012 只对强制先修排序 | 并修与建议先学边不产生层次；`test_order_only_uses_prerequisites_and_reports_alternatives` |
| C012 可替换层次 | 同层多课程时 `alternatives=true`；同一测试 |
| C013 未满足条件明确 | `unmet` 逐条列出先修／并修未满足项；`test_next_courses_separates_unmet_prerequisites_from_recommendations` |
| C013 建议先学不误当阻塞 | 建议项只进入 `recommended`，课程仍 `available=true`；同一测试 |
| C014 不冒充选课／毕业审核 | `graduation_audit=false`，limitations 明示；`test_semester_plan_uses_known_terms_and_credit_limit` |
| C014 缺少规则时降级 | 学分未知课程给出告警且不计入上限；无法排入课程进入 `unplaced` 并给出原因 |

## 验证记录

- `uv run pytest tests/test_course_graph_api.py -q`：7 passed。
- `uv run pytest tests/test_course_graph.py tests/test_course_graph_api.py -q`：10 passed。
- `uv run ruff check .` 与 `uv run ruff format --check .`：通过。

## 边界

- 并修只尝试安排在同一学期；建议先学不参与拓扑层次。
- 学期建议只依据强制先修、已知开课季节与学分上限，不代表可选到课或满足毕业要求。
- 关系候选在核对并转换前不会成为正式边，只作为缺项／待核对问题阻塞完整性。
