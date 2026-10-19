# C007 已修、在修与待修状态维护

## 交付内容

- 新增 `course_study_records`：按方案课程保存修读记录，状态只允许 `completed`、`in_progress`、`planned`。
- 学期可选；同一课程可在不同学期重复修读，但同一课程、学期与状态只允许一条记录（`NULLS NOT DISTINCT`，未填学期同样受约束）。
- 成绩 `grade` 与已得学分 `credits_earned` 只保存用户显式填写的值；修改状态、创建记录或汇总都不会按课程学分推算。
- 状态 `unknown` 只由“没有修读记录”派生，不能通过接口写入。
- API：
  - `GET/POST /api/v1/curriculum-plans/{plan_id}/study-records`
  - `PATCH/DELETE /api/v1/curriculum-plans/{plan_id}/study-records/{record_id}`
  - `GET /api/v1/curriculum-plans/{plan_id}/study-status`
- 汇总按“已完成 > 在修 > 待修 > 未知”逐门课程聚合，并单列 `record_count`、`repeat_record_count` 与各状态学期编号；汇总不计算或展示推断的学分、成绩。
- 培养方案切换预览的 `study_record_count` 现在统计真实修读记录。
- 迁移 `c007_course_study_records`，`down_revision = c006_course_aliases`。

## 验收对应

| 验收条件 | 实现与证据 |
| --- | --- |
| 修改状态不伪造学分或成绩事实 | PATCH 只更新显式提交字段，未提交字段保持原值；课程学分不参与计算。`test_status_change_does_not_fabricate_credits_or_grade` 断言状态改为 `completed` 后成绩与学分仍为 `null`，且汇总不返回推断字段；`test_status_change_keeps_user_provided_values` 断言用户填写的成绩、学分和备注不被状态修改覆盖。 |
| 课程重复修读和未知状态有规则 | 同一课程可在不同学期分别保存；同课程、学期、状态重复返回 400，未填学期也不能绕过唯一约束；无记录课程汇总为 `unknown` 且文案说明“不代表未修或已修”，`unknown` 不能写入。证据：`test_repeat_study_across_semesters_is_kept_separately`、`test_duplicate_same_term_and_status_is_rejected`、`test_unknown_status_is_derived_from_no_records`、`test_aggregate_precedence_is_completed_in_progress_planned_unknown`。跨方案与跨空间隔离由 `test_cross_plan_course_is_rejected`、`test_records_are_isolated_by_workspace` 覆盖；方案切换预览计数由 `test_switch_preview_counts_study_records` 覆盖。 |

## 验证记录

- `uv run ruff check src/campusflow tests/test_course_study_records.py tests/conftest.py`：All checks passed。
- `CAMPUSFLOW_TEST_DATABASE_URL=postgresql+psycopg://campusflow:campusflow@127.0.0.1:5432/campusflow_c007_test uv run pytest tests/test_course_study_records.py -q`：9 passed。
- 相关回归：同一隔离库执行 `tests/test_course_paths_profile.py tests/test_course_relations.py tests/test_course_aliases.py tests/test_course_matching.py`：62 passed。
- 隔离库迁移升级成功，`alembic_version` 为 `c007_course_study_records`；`course_study_records` 唯一约束显示为 `NULLS NOT DISTINCT`，状态与学分检查约束存在。

说明：共享测试库当时停留在另一工作树分支的 `c003_curriculum_candidates` 版本，本分支无法识别该 revision，因此验证使用独立数据库 `campusflow_c007_test`，不干扰并发任务。

## 边界

- 本项不包含修读状态界面（C008）、课程关系图与拓扑排序（C009/C010）以及毕业资格审核。
- 汇总不计算 GPA、总学分或“是否满足毕业要求”；只展示用户填写的记录与派生状态。
- 同一课程同一学期若同时存在多条不同状态记录，保持为独立事实，汇总按优先级取最高状态；如需合并或清理由后续修读规划功能处理。
