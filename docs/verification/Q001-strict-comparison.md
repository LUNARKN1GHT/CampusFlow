# Q001 严格引用输出复测

用户选择明确输出要求并保留严格检查，不增加位置到编号的转换。第二轮使用 `q001-source-id-v2`：三个字段必须返回、空项写 `[]`，引用只允许输入的编号清单，并明确禁止用 `locator` 替代 `id`。四组各 9 题、重复两次，2026-10-15 共完成 72 份回答，无新增网络失败。

证据：[汇总](../evaluation/q001-live-strict-comparison/summary.json)、[完整输出](../evaluation/q001-live-strict-comparison/results.json)、[累计账本](../evaluation/q001-live-strict-comparison/billing.json)、[待人工复核表](../evaluation/q001-live-strict-comparison/answer-review.csv)。

| 检索模型 | 回答模型 | 格式合格 | 引用编号不合法 | 回答状态与预标注不一致 | 格式合格回答 P95 |
| --- | --- | --- | --- | --- | --- |
| Embedding-2 | GLM-4-Flash-250414 | 18/18 | 0 | 4 | 6.207 秒 |
| Embedding-2 | GLM-4.5-Flash | 18/18 | 0 | 4 | 15.738 秒 |
| Embedding-3 | GLM-4-Flash-250414 | 18/18 | 0 | 4 | 5.042 秒 |
| Embedding-3 | GLM-4.5-Flash | 18/18 | 0 | 4 | 6.432 秒 |

原轮 13 份格式不合格、19 份格式合格但引用编号错误，在这次小样本复测中没有再出现；这不构成通用准确率保证，也不能替代语义复核。耗时仅是本次未随机交错运行的诊断，口径与限制见 [首轮比较](Q001-first-comparison.md)。

剩余 16 个状态差异均来自 `missing-year-time` 和 `partial-coverage`：预标注要求 `uncertain`，模型均返回 `insufficient`。例如已知实验主题但缺少提交格式时，回答保留已知内容并写出了缺失原因，却仍将整条结果标为资料不足。需要补充状态定义，并对这两个场景做针对性复测；不应凭空修改预标注来提高成绩。

第二轮从原账本继承 170 次调用，新增 166 次，累计 336 次；已知用量估算 **0.0013990 元**，累计保守预留 **0.7454720 元**。仍只有历史第 51 次请求未返回用量；实际账单未核对，未知记录没有清除。已验证新账本前 170 条与首轮完全相同。

独立人工语义复核和复杂检索排序仍未完成；当前样本不足以区分两个向量模型，Q001 继续保持开放。

后续补充：明确 `uncertain` 与 `insufficient` 的区别后，使用 v3 仅对上述两个场景、四组各两次做 16 份定向复测，格式、编号和状态全部符合预期。见 [定向汇总](../evaluation/q001-live-status-comparison/summary.json)。这不是对全部九题的完整第三轮回归；其他内容仍需独立复核。
