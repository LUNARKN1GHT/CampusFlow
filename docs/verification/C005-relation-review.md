# C005 课程关系人工核对与确认

## 交付内容

- 课程与关系候选复用现有核对链路：逐字段修正、确认、忽略、退回待核对、原文证据入口均按 `curriculum_course` / `course_relation` 类型工作。
- 详细接口返回 `scope.curriculum_plan_id`，确认与转换都不会丢失适用方案。
- `POST /api/v1/candidates/{id}/convert` 新增两条分派：
  - 课程候选 → 方案课程，保存 `source_candidate_id`，重复转换返回既有行。
  - 关系候选 → 正式关系，保留方向、类型、条件原文、方案与来源候选。
- 关系中的课程通过正式课名或显式别名严格匹配；无法匹配或存在歧义时返回 400，不写正式关系，候选保留可修正后重试。
- 已忽略或未确认候选不能转换。

## 验收对应

| 验收条件 | 实现与证据 |
| --- | --- |
| 确认关系不会丢失适用方案 | 转换时读取候选 `curriculum_plan_id`，正式关系保存同一方案；`test_relation_candidate_conversion_keeps_type_and_plan`、详情 scope 断言 |
| 缺失课程或矛盾来源可保留待核对 | 缺失课程转换失败但候选与修正入口保留；修正后可重试成功。歧义别名返回 `ambiguous` 且不落库；`test_missing_course_stays_reviewable_and_can_be_corrected`、`test_ambiguous_alias_blocks_relation_conversion` |

## 验证记录

- `uv run pytest tests/test_curriculum_conversion.py -q`：6 passed。
- 覆盖课程转换幂等、关系类型与方向、缺失课程修正重试、歧义别名、忽略候选、详情方案作用域。
- `uv run ruff check src/campusflow tests/test_curriculum_conversion.py`：通过。

## 边界

- 修正只能追加记录，原始提取与证据不被改写；用户修正后的值不能声明为原文规定。
- 关系冲突与缺失课程的图级汇总由 C011 负责；本任务只保证转换入口不伪造正式关系。
