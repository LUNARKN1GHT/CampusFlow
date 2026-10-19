# C017 课程成环、缺项与范围隔离验收

## 验收样本

- `docs/verification/samples/course-paths/sample-plan.json`：同一专业两个版本的合成方案（2024 版、2025 修订版），含课程、三类关系、环、自环、方案外先修课程、来源矛盾与未知字段。
- 2024 版预期可完整排序；2025 修订版预期因强制先修环、自环、缺项与来源矛盾阻塞。

## 执行结果

| 场景 | 预期 | 实际与证据 |
| --- | --- | --- |
| 2024 合法顺序 | 每条强制先修都被满足 | `/order` 未阻塞；按层次展开后每条 `prerequisite` 的前置层级严格更小 |
| 2025 多节点环 | 返回 CS401→CS402→CS403→CS401 | `/graph` 与 `/order` 返回闭合 `path` 及三条关系编号 |
| 2025 自环 | CS404→CS404 显式报告 | `path == [CS404, CS404]`，未静默丢弃 |
| 2025 方案外先修 | CS499 作为缺项展示，不补课、不删关系 | 未匹配关系候选进入 `missing`，带候选编号；顺序与学期方案阻塞 |
| 2025 来源矛盾 | CS210→CS211 强制先修与建议先学并列，不自动择一 | 两条正式关系触发 `relation_conflict`，图不完整 |
| 未知字段 | 只提示，不影响依赖顺序 | CS215 学分未知、CS209/CS215 开课学期未知；2024 顺序不受影响，学期建议给出学分未知告警 |
| 版本范围隔离 | 两版同名课程与关系不混接 | 同名课程编号不同；两版边集合互不相交；2024 图完整、2025 图阻塞 |
| 结论边界 | 不宣称毕业或选课审核 | `/semester-plan` 固定 `graduation_audit=false` 并返回限制说明 |

## 验证记录

- `uv run pytest tests/test_course_path_acceptance.py -q`：3 passed。
- `uv run pytest tests/test_course_graph.py tests/test_course_graph_api.py tests/test_course_path_acceptance.py -q`：13 passed。
- `uv run ruff check .` 与 `uv run ruff format --check .`：通过。

## 未覆盖

- 真实教务培养方案与真实选课系统未接入；学分与开课学期仅按样本已知值判断。
- 并修同学期只给出告警，不作为排课成功保证。
