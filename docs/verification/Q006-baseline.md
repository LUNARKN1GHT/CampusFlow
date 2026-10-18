# Q006 关键词与向量检索基线

日期：2026-10-18

## 交付

- `domain/ranking.py` 提供 `keyword` / `vector` / `hybrid` 三种排序。关键词按中文二元字组与拉丁词切分，课程编号、日期等精确线索可直接命中；向量分只在同一 `EmbeddingProfile` 内比较；混合排序使用 RRF（k=60），不把不可比的分数直接相加。
- 排序键固定为 `(-score, material_id, version_id, block_id)`；相同输入重复调用顺序一致，已由测试覆盖。
- 向量/混合模式必须显式传入同一配置的查询向量；没有候选或关键词模式零命中时返回空结果，由 Q012 说明“现有资料中未发现”。

## 验收证据

- 可复现：`tests/test_qa_retrieval.py::test_vector_and_hybrid_ranking_are_reproducible_and_validate_inputs`；`tests/test_qa_evaluation.py::test_evaluation_is_reproducible`。
- 缺少课程名语义时的关键词样本：`tests/test_qa_retrieval.py::test_keyword_ranking_uses_course_number_even_without_semantic_vectors`；评估集中 `ddl-course-code` 与 `course-code-only` 两个编号样本。
- 评估集三种模式结果（确定性假向量，不联网）：关键词 recall@3 1.0、引用精确率 0.2667、MRR 0.8；向量 0.8 / 0.2 / 0.4；混合 1.0 / 0.2667 / 0.6667；跨范围错误均为 0。

```bash
cd backend && uv run --locked pytest tests/test_qa_retrieval.py tests/test_qa_evaluation.py -q
backend/.venv/bin/python scripts/evaluate_qa_retrieval.py
```

## 限制

- 假向量不包含课程名语义，只证明排序可复现与关键词基线可评估；真实向量模型对比需配置 `ZHIPU_API_KEY` 后使用 `--embedder zhipu`。
- Q010 的评估集与指标见 [Q010-retrieval-evaluation.md](Q010-retrieval-evaluation.md)。
