# C004 强制先修、并修与建议先学

## 交付内容

- 新增 `curriculum_courses`：方案内课程名称、代码、学分与已知开课季节；同名课程在同一方案内唯一。
- 新增 `course_relations`：方向（from → to）、关系类型（`prerequisite`／`corequisite`／`recommended`）、适用方案、条件原文与来源候选。
- API：课程增删改查、关系增删改查，可按关系类型单独查询。
- 关系两端必须属于同一方案；跨方案同名课程不能互接。自环允许保存，交由 C010 显式报告。
- 被关系引用的课程不能静默删除，返回 400 要求先删除相关关系。
- 只有 `prerequisite` 会在后续拓扑排序与“下一步可学”中作为硬约束；`recommended` 仅作提示。

## 验收对应

| 验收条件 | 实现与证据 |
| --- | --- |
| 三种关系可分别查询 | `GET /curriculum-plans/{id}/relations?relation_type=`；`test_three_relation_types_are_queryable_separately` |
| 建议先学不成为阻止修读的硬约束 | 关系类型分列保存；C012/C013 只把 prerequisite 计入硬约束，recommended 单独输出 |
| 不同方案课程不混接 | 关系创建校验两端均属于目标方案；`test_relation_rejects_cross_plan_course` |

## 验证记录

- `uv run pytest tests/test_course_relations.py -q`：6 passed。
- `uv run ruff check src/campusflow tests/test_course_relations.py`：通过。
- 迁移 `c004_course_relations` 在测试夹具中升级成功。

## 边界

- 本任务不提供文档提取；候选转换由 C003/C005 接入。
- 关系冲突（如同一对课程既强制先修又并修）由 C011 在图分析时报告，不在写入时静默改写。
