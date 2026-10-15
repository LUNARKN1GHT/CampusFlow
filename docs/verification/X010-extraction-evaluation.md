# X010 候选提取与不确定信息识别的评测

任务：[X010 #117](https://github.com/LUNARKN1GHT/CampusFlow/issues/117)，父 Issue #11。作者：金嘉睿（24151735）。2026-10-15。

本项按 B017（`docs/quality/evaluation-metrics.md`）口径，对候选提取逐项记录**正确、遗漏、误提取和缺失字段识别**的结果，四类困难样本各自给出实际数字。核心要求是：**不用解析成功率代替事实提取准确性**。

用户已拍板：先用**假模型**跑（Fake 适配器，返回事先写好的固定回答），产出 B017 格式记录，记录里标明配置是 `fake`，不冒充真实模型成绩。

## 一、样本与人工标注

样本清单 `samples/evaluation/extraction-cases.json`，`sample_set_version` 为 `b011-b012-2026-09-29`，取自 B011/B012 已归档的期望文件。**一个来源版本 = 一个评测样本**，共 7 个 case、9 个样本：

| 难度类别 | case | 来源 | 考的是什么 |
| --- | --- | --- | --- |
| partial-coverage | chat-truncated | CF-IMG-004 | 群聊截图被边界切断，最后一条消息不完整 |
| partial-coverage | blurred-notice | CF-IMG-002 | 模糊区域 + 声明的第 2 页缺失 |
| partial-coverage | missing-pages | CF-IMG-003 | 3 页只有第 2 页 |
| relative-date | relative-date | CF-TXT-001 | 「下周三 18:00」这类相对日期 |
| missing-field | missing-year-time | CF-TXT-002 | 「10 月 15 日」缺年份、缺时刻 |
| cross-source | conflicting-notices | CF-TXT-005/006 | 两份通知对同一字段取值不一致 |
| cross-source | same-name-different-sections | CF-TXT-003/004 | 同名课程不同教学班，不得互相比较 |

每个样本记录三样东西：

- `expected_facts`：人工要求提取的字段和**正确取值**；
- `model_facts`：假模型给出的回答（基准配置下与 `expected_facts` 相同）；
- `expected_uncertain`：人工标注**必须待核对**的不确定单元。

`reason_units` 把「核对原因代码」映射成「不确定单元」，例如 `missing_year → deadline.year`、`source_incomplete → source.coverage`。映射表就是评测范围的边界：表里没有的原因代码（如 `missing_deliverable`、`missing_estimated_minutes`）不进 UNC 的分母——**耗时是规划建议不是事实**，产出物缺失的可核对性由 R 系列界面承担，不在这里重复计分。

两条值得写下来的标注口径：

1. **`due_text` 整体缺失时，年份和时刻也标注为不确定**。系统的缺失检查在此情形下会同时给出 `missing_year` 与 `missing_time`，这不算误报：截止时间整个未知，年份与时刻当然也无从确定。
2. **原文没提提交方式的来源，标注 `task.submission_method`**。X003 对此会给出 `missing_submission_method`，与 B011 对 CF-IMG-004 的标注（`submission channel`）一致，所以人工标注认它，不算误报。

## 二、评测怎么跑

`scripts/evaluate_extraction.py`，仓库根目录执行：

```powershell
uv run --locked --project backend python scripts/evaluate_extraction.py
```

脚本不连数据库、不联网：片段在内存里构造，然后走**与线上完全相同**的代码——`generate_candidates`（X002 结构校验）→ `check_task_fields`（X003 字段检查）→ `with_coverage_reasons` / `stored_uncovered_ranges`（X008 覆盖范围）→ `field_conflicts`（X008 跨来源冲突）。模型那一层换成 `FakeTextGenerator`，回答取自样本清单里写好的 `model_facts`。

每次运行固定记录：被测提交、`config_id`、`component_version`、样本清单版本（B017 第 6 条）。**解析在本评测中固定为「已成功」**，解析成功率不进任何指标——这正是「不以解析成功率代替提取准确性」的落实方式。

三个配置，样本、片段、解析结果完全相同，**差别只在模型回答质量**：

| config_id | 差别 |
| --- | --- |
| `fake-baseline` | 假模型按人工标准作答，用于验证计数口径本身 |
| `fake-degraded` | 漏提产出物、答错并丢掉时刻、把助教的答复当成截止要求、忽略未覆盖范围 |
| `fake-invalid-output` | 其中一个来源的回答不合 JSON 契约（结构性失败） |

`fake-degraded` 的具体注入（都在代码里列明，不是黑箱）：

- `("blurred-notice", "来源版本 #21")` → `deliverable` 置空（漏提）、`due_text` 截成 `2026 年 10 月 16 日`（丢时刻）；
- `("chat-truncated", "来源版本 #11")` → `due_text` 改成 `请以课程平台为准`（误提取：把答复当截止要求）；
- `inject_coverage: false` → 整批不注入「来源未完整解析」的核对原因。

## 三、计数规则

规则本体在 `application/extraction_evaluation.py`，是纯函数，不读库不调模型，所以计数口径可以脱离跑分过程单独测试和复核。

- **分子分母都是原始整数**，`value` 由两者算出（B017 第 2 条），不用比值反推。
- **分母为 0 时 `value` 留空并记 `not_applicable`**（B017 第 3 条），不写成 0% 或 100%。
- **结构性失败按未命中计入，绝不剔除**（B017 第 4 条）：失败样本仍留在 EXT-RECALL 的分母里——它没产出，人工要求的字段就是没提到；它不进 EXT-PRECISION 的分母，因为**没有可判对的提取**。失败数同时记在 `failure_count` 里，两个地方都能看到，不是把失败藏到分母之外。
- **结果按场景切片**（B017 第 6 条）：`slice_key=difficulty`，除四个难度类别外还有一片 `all` 覆盖全部样本。
- 本项 `timeout_count` 恒为 0（假适配器不会超时），`excluded_count` 恒为 0、`exclusion_reason` 留空——**没有任何样本被排除**。

指标口径（`docs/quality/evaluation-metrics.md`）：

| 指标 | 分子 | 分母 |
| --- | --- | --- |
| EXT-RECALL | 取值与人工预期一致的已提取事实字段数 | 人工要求提取的字段总数 |
| EXT-PRECISION | 已提取字段中与人工预期一致的字段数 | 系统输出的事实字段总数 |
| UNC-RECALL | 系统标出且人工也标注的不确定单元数 | 人工标注的不确定单元总数 |
| UNC-PRECISION | 系统标出的单元中人工确需核对的数量 | 系统标出的不确定单元总数 |

**提了但提错**不算命中：召回不加分，准确率还把它算成错误输出。**漏提**只压召回。**漏标待核对**压 UNC-RECALL，**误报待核对**压 UNC-PRECISION。

## 四、实际结果

记录文件 `docs/quality/extraction-evaluation-records.csv`（60 行 = 3 配置 × 4 指标 × 5 切片），逐样本证据 `docs/evaluation/x010-extraction-evidence.json`（含每个样本的命中、遗漏、误提取、漏标与误报明细）。

### fake-baseline

| 指标 | 全部样本 | partial-coverage | relative-date | missing-field | cross-source |
| --- | --- | --- | --- | --- | --- |
| EXT-RECALL | 22/22 = 1.0000 | 6/6 | 3/3 | 3/3 | 10/10 |
| EXT-PRECISION | 22/22 = 1.0000 | 6/6 | 3/3 | 3/3 | 10/10 |
| UNC-RECALL | 20/20 = 1.0000 | 12/12 | 2/2 | 2/2 | 4/4 |
| UNC-PRECISION | 20/20 = 1.0000 | 12/12 | 2/2 | 2/2 | 4/4 |

`run_status = completed`，`failure_count = 0`。满分是应该的：这个配置的用途是证明**口径本身可跑通、分母不是空的**，不是宣称系统效果。

### fake-degraded（解析结果完全没变，只是回答变差）

| 指标 | 全部样本 | partial-coverage | 其余三个切片 |
| --- | --- | --- | --- |
| EXT-RECALL | 19/22 = 0.8636 | 3/6 = 0.5000 | 均为 1.0000 |
| EXT-PRECISION | 19/21 = 0.9048 | 3/5 = 0.6000 | 均为 1.0000 |
| UNC-RECALL | 16/20 = 0.8000 | 8/12 = 0.6667 | 均为 1.0000 |
| UNC-PRECISION | 16/18 = 0.8889 | 8/10 = 0.8000 | 均为 1.0000 |

`run_status = completed`，`failure_count = 0`。**解析成功率和上一行一模一样，分数却掉下来了**——掉的正是提取准确性：漏提的 `deliverable`、答错的 `due_text` 进 `missing_facts` / `wrong_facts`，被忽略的未覆盖范围进 `unflagged_uncertain`，多出来的 `deadline.unrecognized` 与 `deadline.time` 进 `overflagged_uncertain`。切片也说明问题集中在 `partial-coverage` 这一档。

### fake-invalid-output（一个来源结构性失败）

| 指标 | 全部样本 | cross-source | 其余三个切片 |
| --- | --- | --- | --- |
| EXT-RECALL | 19/22 = 0.8636 | 7/10 = 0.7000 | 均为 1.0000 |
| EXT-PRECISION | 19/19 = 1.0000 | 7/7 = 1.0000 | 均为 1.0000 |
| UNC-RECALL | 18/20 = 0.9000 | 2/4 = 0.5000 | 均为 1.0000 |
| UNC-PRECISION | 18/18 = 1.0000 | 2/2 = 1.0000 | 均为 1.0000 |

`run_status = partial`，`failure_count = 1`。失败样本的 3 个字段仍留在 EXT-RECALL 分母里按未命中计（10 → 7 命中），**没有被排除**；它的 UNC 期望单元同样按漏检计（`2/4`）。EXT-PRECISION 与 UNC-PRECISION 的分母没有因此缩小——没有产出就没有可判对的提取，这是 B017 第 4 条的读法，而不是统计上的放水。

另外，失败的那一侧不再产出候选，冲突因此**消失**：`conflicting-notices` 的另一侧也拿不到 `deadline.conflict`（UNC-RECALL down 到 0/1）。这是符合预期的：只有一个取值时本来就不存在冲突，不能靠"标记冲突"掩盖少了半边来源。

### 四类困难样本的覆盖情况

| 难度类别 | 样本数 | 是否给出实际结果 |
| --- | --- | --- |
| partial-coverage | 3 | 是，且是唯一在 degraded 配置下掉分的切片 |
| relative-date | 1 | 是 |
| missing-field | 1 | 是 |
| cross-source | 4 | 是，且是唯一在 invalid-output 配置下掉分的切片 |

## 五、验证与限制

`tests/test_extraction_evaluation.py` 覆盖：比值由整数分子分母算出；提了但提错不算命中且压准确率；漏提只压召回；**结构性失败仍留在召回分母、不被剔除**；分母为 0 时留空记 not_applicable；漏标与误报分别压召回和准确率；切片含 `all` 与每个难度类别；耗时不算事实字段。另有 5 项检查样本清单本身：标注单元都能由某个核对原因产生、假模型回答逐字取自片段、基准预期不超出模型给出内容、四类困难样本齐备、case 声明的样本编号与来源数一致。

2026-10-15：新增 13 项测试，完整后端测试为 730 passed（4 个既有警告）；Ruff 检查、格式检查通过。

限制，必须写清楚：

- **这是假模型口径，不是真实模型成绩**。记录里的 `config_id` 带 `fake-` 前缀，`notes` 也写明模型为假适配器。真实模型（用户选定的 `glm-4-flash-250414`）的跑分需要额度，属于后续单独一次运行，会以新的 `config_id` 追加记录，不改这一份。
- **阈值仍未定**。B017 里各项阈值状态是 `deferred_until_baseline`，本项只提供分母与计数方式，不提出阈值。
- **本项只测 4 项指标**。CIT-SUPPORT、PLAN-*、EDIT-COST、REMINDER-*、PERF-LATENCY、MODEL-COST 不属于提取环节，未生成记录行；这一点写在 CSV 的 `notes` 里，不靠空白行暗示。
- **对照实验是脚本内注入的**，不是真实模型的不确定性。它证明的是「同一解析结果下指标会因提取质量而变」，不证明真实模型的分数分布。
- **冲突单元未按字段细分**：本批样本里唯一冲突的字段是 `due_text`，所以 `deadline.conflict` 是一个单元；样本集扩大后应拆到字段粒度。
- 记录由脚本自动生成，`reviewed_by` 留空，人工抽查后再补。

## 六、分支与交付

前置 X001 #108、B011 #39、B012 #40、B017 #45 均已完成。X008 的成果（本评测调用的覆盖范围与冲突比对）在本分支的基底提交里，所以 X010 从 `issue/#115` 继续，PR 目标为 `issue/#115`（当前链条栈顶），待 X008 合入 develop 后由 GitHub 自动改指 develop。本项只交付 #117，不关闭父 Issue #11，也不代替 R 系列的人工核对界面。
