# N015 识别重复候选任务与日程

作者：李亦凡；任务 #165；父任务 #19。前置 X001、R004、N010。

## 已实现

`GET /api/v1/candidates/{candidate_id}/duplicates` 只读返回同空间、同类型的重复建议：

- 先比较候选声明的适用范围：学期、课程或教学班任一已知且不同即排除，并给出原因代码 `different_semester` / `different_course` / `different_class`。
- 标题相同、时间字段相同或内容高度相似时计算分数；只有同时命中标题或时间，且总分不低于 0.6 才作为建议。
- 每条建议返回分数、原因、命中字段、来源版本和候选核对状态。
- 始终返回 `auto_merge=false`，不删除、不合并、不修改候选状态；用户需在 N018 界面预览后再决定。

## 验收对照

| 验收条件 | 证据 |
| --- | --- |
| 同名不同学期事项不被当作同一项 | `test_same_name_in_different_semester_is_excluded`：同名同学期不同时，建议列表为空且对方出现在 `excluded`，代码为 `different_semester`。 |
| 重复建议不自动删除或合并 | 响应固定 `auto_merge=false`；`test_same_scope_and_content_suggests_but_never_merges` 断言两个候选的核对状态仍为 `pending`。 |

## 验证

```text
backend$ uv run --locked pytest tests/test_duplicates.py -q
3 passed
```

真实 PostgreSQL；无新增迁移、依赖或模型调用。

## 未覆盖与限制

- 评分是保守的规则版本（标题、时间、内容），不调用模型；真实样本阈值留后续评测。
- 只比较同类型候选；已转换正式事项的重复合并由 N016 处理。
- 范围完全未知时仍可给出建议，但标记 `scope_same=false` 并提示人工核对。
