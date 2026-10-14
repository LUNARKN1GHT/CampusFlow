# X003 任务要求与截止原文提取

任务：[X003 #110](https://github.com/LUNARKN1GHT/CampusFlow/issues/110)。作者：金嘉睿（24151735）。父任务：#11。2026-10-13 保存部分成果；2026-10-14 补齐日期完整性检查及专项验证，本文件保留原路径供此前链接使用。合并及任务关闭以 GitHub 审核记录为准。

## 用户选择与交付

用户选择由代码检查常见日期写法，复杂或不能识别的描述进入待核对；不由模型决定日期是否完整。继续使用已选定的智谱文字适配器和 X001 分表模型，没有新增依赖、迁移或配置。

`application/task_extraction.py` 提供 `extract_task_drafts(repos, workspace_id, material_id, version_no, generator)`。通过 X002 先验证空间、原文版本和生成结构，再检查任务字段并返回 `list[CandidateDraft]`。入口没有写库副作用，不创建正式任务，不自动排队；需要保存的调用方显式调用 X001 `save_candidates`，再次验证来源后原子保存，核对状态保持 pending。

| 字段 | 内容 | 出处与限制 |
| --- | --- | --- |
| title | 当前任务名称原文 | document，不可为空 |
| submission_method | 提交方式及相关限制原文 | document；缺失填 null 并保留原因 |
| deliverable | 产出物及要求原文 | document；缺失填 null 并保留原因 |
| due_text | 当前任务截止要求原文 | document；不混入发布日期、学年和其他任务日期，不转换日期 |
| estimated_minutes | 可修改耗时建议 | suggestion、正整数分钟、有说明；无法估计则 null 和原因 |

有值的四类原文字段必须是自身证据摘录中的连续文字；X002 还会检查摘录在真实片段中存在。缺失字段由代码补充 `missing_<字段名>` 核对原因，有耗时建议则补充 `estimated_duration`。模型不能用同名核对原因覆盖这些提示。所有候选校验通过才返回；无效批次不返回部分结果。

## 日期完整性检查

纯领域函数 `domain/deadline_completeness.py::deadline_review_reasons` 只读取 due_text，不读取发布时间、导入时间、系统时间或其他任务字段。检查时统一全角字符，但保存的原文及证据保持不变。

识别常见阿拉伯数字写法：年月日/号、YYYY-MM-DD、YYYY/MM/DD、YYYY.MM.DD、月日部分日期、HH:MM（可带秒）、数字点/时及分/半。年份必须与月日关联；无年份的 2 月 29 日只检查月份日期可能性，不补具体年份。明确的 ISO `T` 时间偏移或 UTC/GMT 偏移不当成第二个时刻；其他含糊写法保留人工核对提示。

| 情况 | 核对原因（均指向 due_text） |
| --- | --- |
| 缺少可识别的截止年份 | missing_year |
| 缺少有效具体时刻 | missing_time |
| 无效日期或时刻，如非闰年 2 月 29 日、24:00 | invalid_date / invalid_time |
| 多个日期、日期区间或多个时刻 | ambiguous_date / ambiguous_time |
| 下周三、周次、月底等相对描述 | relative_date |
| 日期写法暂不能识别 | unrecognized_date |
| 上午/下午、近似时刻、暂定/变更等限定 | deadline_needs_review |

例如“10 月 15 日”保留 missing_year、missing_time；“下周三 18:00”保留 missing_year、relative_date；“2026 年 10 月 8 日 18:00”不标记缺年份或时刻，但仍是待核对候选。识别失败表示需人工核对，并不声称原文肯定没有相关信息。

本项不产生 due_date、due_at 或其他可排期日期，不换算相对日期；X006 可复用 due_text、原文证据和核对原因，再根据发布日、校历等有效依据处理。X007/X008 继续负责范围歧义与来源冲突，不在这里擅自决定生效版本。

## 验收证据

`tests/test_task_extraction.py` 与 `tests/test_deadline_completeness.py`：**47 passed**。

- B012 `missing-year-time.txt`：原文发布日期含年份，截止原文仍只取“10 月 15 日”，两个缺失标记均保留；保存、重新读取仍是 pending。
- B012 `relative-date.txt`：保留“下周三 18:00”和相对日期提示；不在 X003 提前执行 expected.json 中属于 X006 的日期换算。
- B010：测试直接复用生成器通知中的四类文字，核对内容与生成器一致；分段证据独立，提交方式中的“邮件和群聊文件不计为提交”限制保留，明确年份/时刻不被误标缺失。这不是 PDF 解析或真实模型效果测试。
- 其他覆盖：缺提交方式/产出物/截止原文/耗时、无依据日期和提交方式、零或负耗时、模型伪装事实出处、覆盖本地提示、核对原因过多、批次失败、跨空间拦截、多任务证据独立、空结果无写入、非法日期、闰日、全角时间、时区与时间区间。

2026-10-14 完整后端回归 **530 passed**；Ruff check、format --check、前端类型检查与构建、git diff --check 均通过。初次联合测试因 Docker 未启动导致数据库连接超时，启动现有容器后复验通过。测试使用独立 PostgreSQL `campusflow_x001_test` 和假模型，不调用真实智谱。真实模型语义支持、遗漏、跨段提取及复杂通知效果仍由 X010 样本评测验证；合法引用不等于结论正确。当前连续摘录限制会拒绝拼接改写，跨段要求需要保留原文证据并后续评估。

从 backend 运行（先将 CAMPUSFLOW_TEST_DATABASE_URL 指向独立测试库）：

```powershell
uv run --locked pytest tests/test_task_extraction.py tests/test_deadline_completeness.py -q
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
```

前端运行 `npm run build`。API/Worker 自动触发、R 系列核对页面、X004–X010 不由本任务一并验收。

## 分支与审阅

分支 issue/#110，目标 issue/#11。2026-10-14 已将父分支快进同步到 develop d813e90，并将更新合入当前分支。前置 X001 PR #355、X002 PR #356 尚未合并；先审查前置，再合并本项。完整 PR 差异可能暂含前置代码，审查时以本项独立提交为范围；不自行批准或合并。
