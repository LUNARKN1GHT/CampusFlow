# Q007 稳定引用 ID 与候选集校验

日期：2026-10-18

## 交付

- `domain/citations.py` 以 `sha256(material_id:version_id:block_id)` 生成 `cit_` 前缀的稳定引用 ID；引用同时保存版本号、来源片段编号、定位、原文摘录与源指纹。
- `validate_citation_ids` 只接受本次候选集内的编号：未知编号、重复编号、超过 20 条均直接拒绝；模型输出中的越界引用被转换为 `ModelOutputError`，接口返回 502 固定文案，不产生答案。
- 合法 ID 只代表候选集归属，不代表语义支持。`support_warnings` 独立检查回答中出现的日期能否在被引原文中找到；生成层把警告放入 `uncertainties`，不会把“ID 合法”当作答案正确。

## 验收证据

- 不存在/不在候选集的引用被拒绝：`tests/test_qa_retrieval.py::test_citations_are_stable_and_only_accept_candidate_members`、`tests/test_qa_api.py::test_query_validates_scope_and_rejects_out_of_candidate_citations`。
- 合法 ID 不等于语义正确：`tests/test_qa_acceptance.py::test_valid_citation_id_does_not_prove_semantic_support`。
- 引用可回到资料版本与片段：接口返回 `material_id`、`version_no`、`source_chunk_id`、`page`/`paragraph`；问答页面据此跳转资料库。

## 限制

- 日期支持检查是保守的文本一致性检查，不判断课程适用范围等全部语义；逐样本人工验收见 [Q018-acceptance.md](Q018-acceptance.md)。
