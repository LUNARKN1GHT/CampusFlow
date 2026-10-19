# Q018 DDL 回答与引用语义一致验收

日期：2026-10-18

## 验收口径

不能只检查引用 ID 是否存在。每个样本同时核对：

1. 回答中的具体日期是否能在被引原文中逐字找到；
2. 引用资料的课程、学期、教学班是否与提问范围一致；
3. 日期不完整（缺年份/时刻）是否明确标注不确定；
4. 多资料冲突是否并列展示并要求核对；
5. 来源删除后旧回答是否不再显示为可验证。

## 样本结果

| 样本 | 原文 | 生成回答 | 核对结果 |
| --- | --- | --- | --- |
| 完整日期 | `第三次作业于2026年10月20日23:59前提交` | 同一日期 | 引用可验证；日期逐字命中；范围 `course_id/class_name` 一致；无支持警告 |
| 日期不完整 | `第三次作业于10月20日截止` | 同一日期 | 返回“原文只写了月日、没有年份”不确定项，不补造年份 |
| 合法 ID 但语义不符 | `2026年10月20日` | `2026年12月31日` | 引用 ID 仍合法，但产生“日期未出现在被引原文”警告，不当作有依据 |
| 多资料冲突 | `10月20日截止` / `10月25日截止` | 并列说明不一致 | `conflicts.requires_review=true`，两条日期与引用均列示，无“已选定”字段 |
| 来源删除 | 旧会话引用同一资料 | 旧回答保留 | 读取时引用 `valid=false`、`status=source_deleted`，不可再验证 |
| 无候选 | 范围内无已索引资料 | 固定“现有资料中没有找到…” | `insufficient=true`、无引用；不调用模型，不表述“没有作业” |
| 部分覆盖 | 解析覆盖 1 处失败 | 正常回答 + 覆盖提示 | `coverage.kind=partial`，显示成功/失败块数与未覆盖数量 |

## 命令与结果

```bash
cd backend && uv run --locked pytest tests/test_qa_acceptance.py tests/test_qa_api.py tests/test_qa_retrieval.py -q
# 57 passed
cd backend && uv run --locked pytest -q
# 1252 passed
cd frontend && npm run build
# vue-tsc + vite build 通过
```

浏览器走查（Q016）确认引用卡可跳到资料库对应版本与片段。

## Q019 真实模型补跑（DeepSeek）

日期：2026-10-18。生成模型为 DeepSeek `deepseek-chat`（OpenAI 兼容接口，新增 `CAMPUSFLOW_TEXT_PROVIDER=deepseek` 装配；适配器见 `backend/src/campusflow/infrastructure/llm/text.py`）。检索向量仍为确定性假向量（作者选择），本补跑只验证真实生成模型的语义表现。

命令（在 `backend/` 目录；固定护栏：整个模块最多 10 次生成调用，实际 4 次；仅使用合成样本，不发送真实校务资料）：

```bash
CAMPUSFLOW_Q018_LIVE=1 CAMPUSFLOW_TEXT_PROVIDER=deepseek CAMPUSFLOW_TEXT_MODEL=deepseek-chat \
  uv run --locked pytest tests/test_qa_acceptance_live.py -q -s
# 6 passed（24.9s）
```

| 样本 | 真实模型回答（摘要） | 核对结果 |
| --- | --- | --- |
| 完整日期 | “数据库原理（CS101）第三次作业的截止时间是2026年10月20日23:59前。” | 日期与原文逐字一致；引用可验证；模型自行补充“原文未说明提交方式” |
| 日期不完整 | “根据候选资料，第三次作业的截止日期为10月20日。” | 不补造年份/时刻；模型与校验代码均给出不确定项 |
| 多资料冲突 | “……一份写10月20日截止，另一份写10月25日截止。” | 两条引用并列、未择一；`conflicts.requires_review=true` |
| 部分覆盖 | 正常回答 + `coverage.kind=partial` | 覆盖提示独立展示（解析未覆盖 1 处）；引用可验证 |
| 无候选 | 固定文案，`insufficient=true` | 0 次模型调用 |
| 来源删除 | 删除后固定文案，`citations=[]` | 0 次模型调用；旧引用不再展示为可验证 |

四项正例各 1 次真实生成调用，均为一次通过；未出现引用编号越界、截断输出或工具调用。按保守估算（输入约千级 token、输出百余 token）总费用远低于 10 元预算。

## 未覆盖与限制

- 上表第一段结果来自确定性假生成器（验证协议与代码校验）；第二段 Q019 补跑使用真实 DeepSeek 生成模型，检索侧仍是确定性假向量，真实 Embedding-3 向量未参与，Q010 指标不因此更新为模型结论。
- 真实模型复核为单模型、单轮合成样本，不构成准确率统计；引用支持率与不确定项召回的统计口径仍沿用 Q001/Q010 评估集。
- DeepSeek 条款澄清前（见 AGENTS.md），不得把真实校务资料发往该接口；本补跑仅用合成样本。
- 日期一致性检查只覆盖可识别日期写法；相对日期、跨页表格等复杂场景仍依赖 X006/X008 的候选核对。
