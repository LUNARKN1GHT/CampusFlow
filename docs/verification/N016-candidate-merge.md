# N016 重复事项合并与关联迁移

作者：李亦凡；任务 #169；父任务 #19。前置 N015 #165、T006 #61。

## 已实现

- `GET /api/v1/candidates/{target}/merge-preview?source_ids=...` 只读预览：目标保留字段、来源候选/版本、正式事项编号、来源进度、计划时间块数量和预览令牌。
- `POST /api/v1/candidates/{target}/merge` 需要预览令牌、来源候选和原因；范围或进度变化后旧令牌返回 409。
- 合并规则：
  - 目标候选与字段保持；来源候选标记 `superseded` 并写入核对变更记录。
  - 目标未开始、来源已进行/完成时采用来源进度；来源任务改为 `cancelled`，原因写明合并目标。
  - 来源任务的计划时间块迁移到目标任务，锁定状态不变。
  - `candidate_merges` 保存来源快照（候选、版本、正式事项、来源进度、时间块数）和保留进度；来源资料不删除。
  - 同一目标与来源重复提交返回既有记录（200），不重复迁移、不重复取消。
- `GET /api/v1/candidates/{id}/merges` 查看合并历史。

## 验收对照

| 验收条件 | 证据 |
| --- | --- |
| 原有进度和来源的保留规则明确 | 预览与记录都返回目标/来源进度和来源快照；`test_preview_and_merge_preserve_progress_sources_and_blocks` 断言来源 `done` 迁移为目标 `done`、来源任务 `cancelled`、锁定时间块迁移到目标任务。 |
| 重试不会重复迁移或丢失历史 | 同一请求第二次返回 `created=false`，历史只有一条；过期预览令牌 409，不执行部分迁移。 |

## 验证

```text
backend$ uv run --locked pytest tests/test_candidate_merge.py -q
2 passed
```

新增迁移 `n016_candidate_merges`（前置 `n014_version_acceptances`）；真实 PostgreSQL。无模型调用。

## 未覆盖与限制

- 只合并同一类型候选；日程与规则没有进度字段，只保留来源快照。
- 不自动选择来源；用户必须在预览中显式提交来源列表。
- 计划时间块只迁移任务关联块，缓冲块保持原位。
