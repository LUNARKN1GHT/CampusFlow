# C006 课程别名与唯一匹配规则

## 交付内容

- 新增 `course_aliases`：别名绑定到方案课程，可附带教学班与学期范围。
- API：别名列表、创建、删除，以及 `GET /curriculum-plans/{id}/aliases/resolve` 严格解析。
- 匹配只接受正式课名或用户显式维护的别名；Latin 字母不区分大小写，不做简称猜测。
- 带教学班或学期范围的别名，只有在查询提供相同范围时才参与匹配；缺少范围不自动套用。
- 同时命中多门课程时返回 `ambiguous`，不自动选择；维护别名只新增映射，不改写 `courses` 或合并教学班。

## 验收对应

| 验收条件 | 实现与证据 |
| --- | --- |
| 别名跨学期或跨班有歧义时不自动匹配 | 范围不满足直接跳过；多结果返回 `ambiguous`；`test_scoped_alias_is_not_used_without_matching_scope`、`test_alias_colliding_with_other_course_is_ambiguous` |
| 维护别名不会合并真实课程 | 别名与学期课程分表；测试确认两门真实课程仍为两条；`test_alias_maintenance_does_not_merge_real_courses` |

## 验证记录

- `uv run pytest tests/test_course_aliases.py -q`：7 passed。
- `uv run ruff check src/campusflow tests/test_course_aliases.py`：通过。
- 迁移 `c006_course_aliases` 在测试夹具中升级成功。

## 边界

- 别名解析不写入绑定，也不改变候选或关系；培养方案匹配的自动调用由 C003/C005 决定。
- 同名列冲突保留为歧义，不通过删除别名或改名静默消除。
