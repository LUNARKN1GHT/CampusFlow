# C002 培养方案版本与个人方案

## 交付内容

- 新增 `curriculum_plans`：专业、入学年级、版本标签共同确定适用方案，保存可选的每学期学分上限和来源资料版本。
- 新增 `learner_profiles`：保存个人专业、年级与当前方案；缺专业、年级或方案时接口返回 `missing_fields`，不把缺失当成完整。
- API：`/api/v1/curriculum-plans`（列表、创建、读取、更新）与 `/api/v1/learning-profile`（读取、保存、切换预览）。
- 同一空间内“专业＋年级＋版本”唯一；个人专业或年级与所选方案不一致时拒绝保存，避免混用不同年级规则。

## 验收对应

| 验收条件 | 实现与证据 |
| --- | --- |
| 不同年级或版本规则不混用 | 方案唯一约束；个人专业/年级与方案不一致返回 400；`test_plan_versions_are_not_mixed`、`test_profile_rejects_mixing_grade_or_major` |
| 缺失版本和专业信息明确待补充 | `GET /learning-profile` 的 `missing_fields` 按 major、grade_year、curriculum_plan_id 返回；`test_profile_reports_missing_fields_until_complete` |

## 验证记录

- `uv run pytest tests/test_course_paths_profile.py -q`：5 passed。
- `uv run ruff check src/campusflow tests/test_course_paths_profile.py`：通过。
- `uv run alembic upgrade head` 由集成测试夹具执行，包含 `c002_curriculum_plans`。

## 边界

- 切换方案只改变个人当前方案；旧方案的课程、关系与修读记录不删除、不迁移。预览中的关系/课程/修读记录计数在后续 C004/C007 接入真实数据。
- 本任务不解析培养方案文档，提取由 C003 负责。
