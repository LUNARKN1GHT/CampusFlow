# C003 培养方案课程和条件候选提取

## 交付内容

- 新增候选类型 `curriculum_course` 与 `course_relation`；候选作用域新增 `curriculum_plan_id`，不同方案版本不混用。
- `POST /api/v1/curriculum-plans/{plan_id}/extract` 只在用户显式请求时调用模型，必须指定资料与版本。
- 课程候选保留课程名、编号、学分与开课学期原文；关系候选保留两端课程名、关系类型原文与条件原文。
- 开课学期按原文季节词归一化为 `fall/spring/summer/winter`，无法识别时保留空值并添加 `offered_terms_unknown`。
- 关系类型必须能确定为强制先修、并修或建议先学；无法确定时整批拒绝，不保存半成品。
- 课程名或关系两端未在方案内匹配时，添加 `relation_course_unmatched` / `curriculum_course_unmatched` 核对原因，仍保存为 pending。
- 所有候选保存后均为待核对；提取不会创建正式课程或正式关系。

## 验收对应

| 验收条件 | 实现与证据 |
| --- | --- |
| 保留原文和范围 | 字段证据绑定当前版本的片段与逐字摘录；候选 scope 保存 `curriculum_plan_id`；`test_extraction_saves_pending_candidates_without_formal_relations` 检查正式表仍为空 |
| 不能确定的关系不直接写成正式依赖 | 关系类型无法识别时返回 400/422 且不落库；未匹配课程保留核对原因；`test_unknown_relation_type_is_rejected_without_saving`、`test_relation_candidate_keeps_unmatched_course_as_review_reason` |

## 验证记录

- `uv run pytest tests/test_curriculum_extraction.py -q`：4 passed。
- 回归：`uv run pytest tests/test_candidate_generation.py tests/test_candidates.py tests/test_candidate_review.py tests/test_course_paths_profile.py tests/test_course_relations.py tests/test_course_aliases.py -q`：113 passed。
- `uv run ruff check src/campusflow tests/test_curriculum_extraction.py`：通过。
- 迁移 `c003_curriculum_candidates` 在测试夹具中升级成功。

## 边界

- 真实模型效果尚未评测；测试使用确定性假生成器验证结构与证据边界。
- 候选确认与转换为正式课程/关系由 C005 接入。
