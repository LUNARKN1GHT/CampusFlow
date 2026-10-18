# Q008 删除、归档与范围变更时的索引失效

日期：2026-10-18

## 交付

- 检索读取沿用 Q005 的 SQL 前置条件：工作空间、学期/课程/教学班、非归档资料、当前存储版本、当前清单内成功块；删除或归档的资料不会进入排序。
- `infrastructure/citation_sources.py` 在读取旧回答时重新核对资料状态，返回 `valid`、`source_deleted`、`source_archived`、`version_superseded`、`source_changed`。来源被改写时，块指纹与原文不一致即判定失效。
- 归档恢复后下一次读取即可重新检索；资料范围修订（学期/课程/教学班）立即按新范围生效，旧范围不再命中。

## 验收证据

- 删除与归档恢复：`tests/test_qa_retrieval.py::test_deleted_and_archived_sources_disappear_from_retrieval`。
- 范围修订：`tests/test_qa_retrieval.py::test_scope_revision_takes_effect_on_next_read`。
- 四种失效状态：`tests/test_qa_retrieval.py::test_citation_revalidation_marks_deleted_archived_superseded_and_changed`、`test_citation_revalidation_rejects_rewritten_source_text`。
- 旧会话展示：`tests/test_qa_api.py::test_conversation_keeps_scope_and_marks_deleted_citation_invalid`。

## 限制

- 删除后的原文文件清理与备份保留策略沿用资料删除流程；本项只保证检索与引用不再把已删除来源当作可验证依据。
- “归档恢复”指用户恢复归档后重新参与检索，不会恢复已删除的行。
