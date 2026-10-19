# N019 通知冲突与变更历史验收

作者：李亦凡；任务 #178；父任务 #19。前置 N017 #173、N018 #174、B012 #40。

## 验收场景

1. **较晚导入的旧通知**：新版本发布时间早于旧版本，即使声明替代也判定 `conflict`；未显式确认时接受返回 400，旧候选保持 `pending`。
2. **冲突未确认不改当前要求**：`applied=false`，候选核对状态和正式事项均不变化。
3. **跨学期同名**：重复建议把不同学期/课程/教学班的候选放入 `excluded`；合并预览直接 400“不能合并”，不把它当作同一项。
4. **缺替代声明**：没有 `replacement_claim` 时返回 `requires_review`，不能直接接受。
5. **接受后可追溯**：显式确认冲突后接受成功；历史记录包含旧/新版本编号、原因、操作者与两侧原文证据。
6. **合并重试**：由 N016 `test_retry_is_idempotent_and_stale_preview_is_rejected` 覆盖；重复提交返回同一记录，不重复迁移。

## 验证

```text
backend$ uv run --locked pytest tests/test_n019_change_acceptance.py tests/test_version_acceptance.py -q
10 passed

backend$ uv run --locked pytest tests/test_candidate_merge.py tests/test_duplicates.py -q
5 passed
```

真实 PostgreSQL；无模型调用。跨学期合并守卫在验收中补齐：`preview_merge` 现在拒绝学期、课程或教学班不同的候选。

## 未覆盖与限制

- 界面级验证由 N017/N018 的 Playwright 覆盖；本任务聚焦 API 行为与历史可追溯性。
- 接受不会自动改写正式任务字段；影响清单由 N013 提供。
- 提醒与外部日历尚未接入，响应明确标记未同步。
