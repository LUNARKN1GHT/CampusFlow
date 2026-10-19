# Q001：离线评测准备

关联任务 [Q001 #128](https://github.com/LUNARKN1GHT/CampusFlow/issues/128)，父任务 #13。用户选择先准备样本和可运行脚本，不调用真实模型。本交付只完成准备部分，**Q001 保持未完成，不据此确定供应商或模型**。

## 本轮交付

- [合成样本](../../samples/synthetic/q001/README.md)：9 个问题、11 个原文片段，覆盖范围隔离、中文不同问法、缺失日期、冲突通知、部分覆盖、条件规定及资料不足。
- [离线脚本](../../scripts/evaluate_qa_preparation.py)：只用 Python 自带功能，不增加安装包，不读取密钥、不联网、不访问数据库。
- [测试](../../backend/tests/test_qa_preparation.py)：验证错误引用、范围越界、重复计分、超时／缺失记录、非法输入和结果文件生成等边界。

三组预设输出用于验证评分逻辑：`fixture-reference` 直接复制预期答案，检查正常计数；`fixture-degraded` 故意加入错误班级、假引用、补造日期、漏报不确定性及失效来源；`fixture-failure` 注入超时、格式错误和缺失记录。**reference 的满分来自预设答案，不能称作模型准确率；degraded 不是供应商比较结果。**

## 如何运行

在仓库根目录执行（已有 Python 3.12 即可，不必启动 API、数据库或 Worker）：

```powershell
python scripts/evaluate_qa_preparation.py --output-dir data/q001/run-001
```

使用项目已有虚拟环境时，将 `python` 换成 `backend\.venv\Scripts\python.exe`。再次执行需更换末尾目录名；脚本拒绝覆盖历史结果。输入文件最大 2 MiB，拒绝重复 JSON 键、无效数值、重复或未知问题编号。

结果包括 `evidence.json`（每题输出、问题列表、计数及输入指纹）和 `records.csv`（沿用 B017 的评测记录表头，可用表格软件打开）。记录包含代码提交、样本版本、脚本和样本文件指纹，便于找到同一版输入重跑；分数由固定输入确定，运行时间和提交号不要求相同。不会写模型速度或费用成绩：逐题 `model_latency_ms`、`model_cost` 均为空，不能用脚本运行时间冒充模型响应时间。

可用 `--cases` 指定同结构的样本，或 `--responses` 提供人工预设输出。后者的结构为：

```json
{
  "mode": "offline_fixture",
  "config_id": "fixture-manual",
  "runs": [{
    "case_id": "deadline-a",
    "status": "completed",
    "retrieved_ids": ["physics-a"],
    "disposition": "answer",
    "claims": [{"text": "须在2026年10月10日20:00前提交。", "citations": ["physics-a"]}],
    "uncertainties": []
  }]
}
```

该示例只提供一题，其余八题会计为缺失失败。完整记录应包含每个问题。`status` 可用 `completed/failed/timeout`；`disposition` 可用 `answer/uncertain/conflict/insufficient`。自定义样本需同时提供与之对应的预设输出；内置三组输出针对本样本的固定编号设计。真实模型记录不应伪装成 fixture 输入。

## 计分和复核边界

复用 [B017 指标定义](../quality/evaluation-metrics.md)，没有新增正式指标或通过门槛：

| 指标 | 本轮计算方式 |
| --- | --- |
| CIT-SUPPORT 引用支持率 | 去重后的事实主张中，文本与预标注事实完全匹配，引用集合一致，引用存在、有效、在当前范围且被本次检索返回的比例 |
| UNC-RECALL 不确定性召回率 | 应提示的原因编号中，实际给出的比例 |
| UNC-PRECISION 不确定性准确率 | 实际给出的原因编号中，属于应提示范围的比例 |

按整体、场景和输入类型分别汇总分子／分母；分母为零时值留空并标 `not_applicable`。同一主张重复输出只计一次且合并全部引用检查，不能用重复答案冲高成绩。检索按原排名截取前三条，再计算不同命中项；重复结果会占用名额。

运行失败、超时、缺失及格式错误均保留在结果中，不排除样本；失败题按预期事实数作为未获支持的分母。没有输出事实的成功记录，其引用分母为零，但预期事实缺失仍记录在覆盖诊断中，不能解释成答对。CSV 的 `run_status=completed` 只代表完成计数，**不表示回答正确或通过验收**。

检索命中数和答案覆盖数仅为辅助诊断，不替代正式指标。未知措辞进入 `unsupported_or_unreviewed_claims`，暂不计为支持，需人工核对后另行记录；当前脚本没有通用语义判断能力。原因编号匹配也不能证明模型解释完整。全局报告保留每题原始预设输出、错误标记、遗漏事实及多报／漏报原因。

## 隐私与未覆盖输入

本轮文件全部为合成资料，仅在本机处理，不上传到任何模型服务，不读取 `.env`，输出不含密钥。样本定位与范围编号也是合成内容。`reviewed_by` 保持空白，待非作者复核，不能以脚本执行代替人工签字。

未来真实比较时，文本转向量服务需要接收选定的资料片段和查询文本，回答服务需要接收问题及检索到的原文；若加入对话上下文，应只发送必要部分。只能使用获授权的当前工作空间资料。供应商的数据保留、训练用途、存储地域、计费及实际可用型号尚未核实，需选定候选服务后查官方说明并记录，不能推定相同。日志不得记录访问令牌或无关个人信息。

本轮没有覆盖真实 PDF／图片解析、真实文本分块、向量生成、数据库检索排名、真实服务故障、网络耗时、计费、多轮回答和大规模中文语料。11 个片段不足以代表真实校园资料分布，`manual-transcript` 也不能代表 OCR 准确率。对原文中恶意指令的防护另有 X009，本轮不替代其验收。

## 验证与后续

2026-10-15 本地验证：专项测试 29 项通过；后端 PostgreSQL 整体回归 887 项通过；Ruff 代码检查与格式检查通过；前端 `npm run build`（含类型检查）通过；样本登记及 B017 指标／记录模板校验通过。回归仍出现现有依赖弃用提示与未登记 integration 标记提示，不影响本次通过结果。

复验命令：在 `backend/` 设置独立测试库 `CAMPUSFLOW_TEST_DATABASE_URL` 后运行 `python -m pytest -q --tb=short -x -p no:cacheprovider`；在根目录运行 `python scripts/validate_sample_registry.py`、`python scripts/validate_evaluation_assets.py`，以及 `python -m ruff check --config backend/pyproject.toml scripts/evaluate_qa_preparation.py`。脚本格式检查同样使用 `--config backend/pyproject.toml`，避免机器全局配置影响结果。

留档运行基于提交 `76dbe52b5e3d04a60dafd97f22407d07a9e7c4ab`，见 [逐题证据](../evaluation/q001-preparation/evidence.json) 和 [计数表](../evaluation/q001-preparation/records.csv)。以下数字只验证预设输出的计数结果：

| 预设组 | 引用支持 | 不确定性召回 | 不确定性准确 | 运行失败／其中超时 |
| --- | --- | --- | --- | --- |
| reference | 8/8 | 6/6 | 6/6 | 0/0 |
| degraded | 4/7 | 2/6 | 2/2 | 0/0 |
| failure | 6/8 | 5/6 | 5/5 | 3/1 |

degraded 的越界、伪造引用和失效来源出现在三题的错误标记中；另外的补造日期、缺失事实及漏报原因反映在支持率、覆盖诊断和不确定性计数中。它的运行失败数为零不代表答案没有错误。failure 保留全部九题，三条失败分别为超时、格式错误和缺失输出。

后续顺序：先复核样本和评分规则；再由用户决定候选模型、可用账号及费用上限；接入真实检索／回答运行器并记录实际费用和耗时；最后按 B017 汇总人工复核结果、形成选型依据。真实运行器应与当前预设自检明确区分，不把供应商名称贴到 fixture 成绩上。完成真实比较之前，不关闭 Q001，也不宣称 Q002／Q003 的选型前提已满足。
