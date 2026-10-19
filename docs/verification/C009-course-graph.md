# C009 适用方案内的课程关系图

## 交付内容

- `GET /api/v1/curriculum-plans/{id}/graph` 只读取当前方案的课程、已确认正式关系与关系候选问题。
- 节点包含课程代码、学分、开课季节、修读状态与记录数；三类边分列 `prerequisite / corequisite / recommended`。
- 每条边保留 `source_candidate_id` 与证据（片段编号、逐字摘录、字段、版本与资料编号）。
- 不同方案的同名课程按方案隔离，不会互相连接。
- 未转换或无法匹配的关系候选进入 `missing` / `pending`，图不会被静默补全。

## 验收对应

| 验收条件 | 实现与证据 |
| --- | --- |
| 不同方案的同名课程不被混接 | 关系端点限定同一方案；`test_graph_distinguishes_three_edge_types_and_scope` 检查第二个方案无边 |
| 三类边在数据输出中可区分 | 输出 `relation_type` 与图例字段；同一测试断言三种类型 |
| 保留边来源 | 转换自候选的边返回证据；`test_graph_edge_keeps_candidate_evidence` |

## 验证记录

- `uv run pytest tests/test_course_graph.py -q`：3 passed。
- `uv run ruff check src/campusflow tests/test_course_graph.py`：通过。

## 边界

- 本任务只提供图数据；环/缺项的阻塞结论由 C010/C011 的响应字段与顺序接口完成。
- 图不包含未确认候选作为正式边；它们只作为待核对问题展示。
