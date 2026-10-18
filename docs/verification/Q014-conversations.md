# Q014、Q017 有范围会话、失效与覆盖提示

日期：2026-10-18

## 交付

- 新增 `qa_conversations` 与 `qa_turns`（迁移 `q014_qa_conversations`）。会话保存当前学期/课程/教学班；每轮保存当时的范围快照、问题、答案、引用、覆盖、对照、冲突与不确定项。
- 追问只使用会话当前范围；切换范围通过 `PATCH /qa/conversations/{id}/scope` 显式完成，并重新做服务端授权。旧轮次保留原范围，不混入其他学期或空间。
- 会话按工作空间隔离；跨空间读取返回 404。
- 读取旧轮次时逐条重新核对来源，已删除/归档/被新版本替代/原文改写的引用返回 `valid=false` 与固定原因，旧回答不会继续显示“可验证”。
- 会话详情同时返回覆盖提示汇总与当前范围 `pending_conflict_count`，界面可进入资料库或待核对。

## 验收证据

```bash
cd backend && uv run --locked pytest tests/test_qa_api.py -q
```

- 范围保持与删除后引用失效：`test_conversation_keeps_scope_and_marks_deleted_citation_invalid`。
- 空间隔离：`test_conversation_is_isolated_by_workspace`。
- 待核对冲突计数：`test_conversation_detail_exposes_pending_conflict_count`。
- 引用状态单元测试见 [Q008-index-invalidation.md](Q008-index-invalidation.md)。

## 限制

- 会话不跨用户共享；MVP 仍为单用户默认工作空间。
- 旧轮次保存引用快照，不保存完整模型提示词；重新验证只针对来源有效性。
