# Q010 检索召回与引用支持评估集

日期：2026-10-18

## 数据

`samples/evaluation/qa-retrieval-cases.json`，`k=3`，逐样本人工标注 `support`：

| 样本 | 考察点 |
| --- | --- |
| `ddl-course-code` | 课程编号 `CS101`、删除资料、相似课程、不同学期 |
| `exam-date` | 考试日期、二班干扰、相似课程 |
| `relative-week` | 教学周相对日期、归档资料、不同学期 |
| `course-code-only` | 近似课程编号 `CS101A` 与 `CS101` 的区分 |
| `no-support` | 只有删除资料包含“补考”，应召回不到支持片段 |

每个样本还包含同范围但无关的课程通知，避免候选集过小导致所有模式都满分。

## 指标（确定性假向量，不联网）

| 模式 | recall@3 | 引用精确率@3 | MRR | 跨范围错误 | 未命中样本 |
| --- | ---: | ---: | ---: | ---: | --- |
| 关键词 | 1.0000 | 0.2667 | 0.8000 | 0 | 无 |
| 向量 | 0.8000 | 0.2000 | 0.4000 | 0 | `ddl-course-code` |
| 混合 | 1.0000 | 0.2667 | 0.6667 | 0 | 无 |

- 跨范围错误定义：排序结果出现不在案例范围内的资料，或出现 `deleted`/`archived` 资料。删除、相似课程、不同学期样本均通过范围检查，错误数为 0。
- 向量模式漏掉课程编号样本，符合“假向量没有课程名语义”的预期；关键词样本因此不能省略。该结果只作基线对照，不冒充真实 embedding 质量。
- 引用支持用人工 `support` 计算召回与精确率；不把“引用 ID 存在”当作支持充分。

```bash
backend/.venv/bin/python scripts/evaluate_qa_retrieval.py
# 配置密钥后可用真实向量：
ZHIPU_API_KEY=... backend/.venv/bin/python scripts/evaluate_qa_retrieval.py --embedder zhipu
```

## 验收

- 指标与错误示例：以上表与脚本 JSON 输出。
- 删除、相似课程、不同学期范围检查：`tests/test_qa_evaluation.py`。
- 真实模型指标尚未运行（本机未配置密钥）；不得把假向量结果当作真实模型结论。
