# X004 固定日程候选提取

任务：[X004 #111](https://github.com/LUNARKN1GHT/CampusFlow/issues/111)，父任务 #11。作者：金嘉睿（24151735）。日期：2026-10-14。

## 用户决定与实现

用户选择“AI 提取后由代码保守检查”：明显的提交/完成截止要求拒绝作为活动；无法排除混合要求时明确失败，等待拆分或核对。复杂通知可能少提取，真实效果另由 X010 评测。沿用智谱文字生成、X002 结构及来源校验、X001 候选模型，没有新增依赖、迁移或配置。

应用入口：`application.event_extraction.extract_event_drafts(repos, workspace_id, material_id, version_no, generator)`。返回 EVENT 类型的 CandidateDraft 列表，不写库、不创建 FixedEvent、不自动排队。调用方按需显式使用 X001 `save_candidates` 保存，重新检查来源后保持 pending；人工确认与正式日程创建由 R 系列处理。

| 字段 | 含义与约束 |
| --- | --- |
| title | 活动名称原文，必填，必须在 activity_text 中出现 |
| activity_text | 当前活动描述和时间原文，必填，必须有证据 |
| start_text | 开始时间原文，缺失为 null 和 missing_reason |
| end_text | 结束时间原文，缺失为 null，不按常见时长推算 |
| location | 地点原文，缺失保留原因 |
| participation_conditions | 参加对象、资格及条件原文；未写不等于无限制 |

全部字段只能标注 document。非空字段必须是对应证据中的连续原文；X002 验证证据编号属于当前输入且摘录真实。开始和结束必须同时属于当前 activity_text，不能从其他活动或作业句子拿来补全。多活动各自保留字段与证据。

## 保守检查与缺失标记

`domain/event_extraction.py` 只处理纯文本，不导入模型或数据库。

- 活动原文需有“举行、考试、讲座、答辩、会议、实验课、比赛”等可识别描述；含提交、完成、报名、预约或截止措辞的活动描述拒绝接受。
- 同时在被引用片段中找出 activity_text 所在的完整语句（按句号、问叹号、分号、换行分隔），避免模型只摘出“学术讲座”，隐藏“报告周五前提交”。活动和作业分属独立语句时可以分别识别；同句混杂则保守拒绝。
- 缺失字段统一补充 `missing_<字段名>`，尤其 missing_start_text / missing_end_text。模型不能用同名原因覆盖程序生成的提示。
- 起止值不含单一有效常见时刻、包含多个时刻或明显无效值时补 `uncertain_event_time`；年份未明确时补 `event_date_context`；相对或限定时间补 `event_time_context`。这些只是核对提示，不是完整日期解析器。
- 起止时间都保留原文，不给“16:00”补开始端日期，不自动继承年份，不计算活动时长，也不判断跨天先后关系。日期换算和正式日程校验留后续用例。
- 任一事项失败，整个批次不返回部分结果；无活动返回空列表。失败不等同于原文没有活动，调用方必须展示错误而不能当空结果处理。

这些规则是用户选择的保守边界，不保证覆盖所有语言和活动表述，也不能证明原文语义分类完全准确。省略活动名、跨片段拼接或混合条件可能被拒绝；真实模型的遗漏、条件支持和复杂通知效果留 X010 评测。

## 验证

`tests/test_event_extraction.py`：**32 passed**。使用合成通知、FakeTextGenerator 和独立 PostgreSQL `campusflow_x001_test`，不调用真实模型。覆盖：

- 正常活动的起止、地点、资格原文及证据保存回读，候选 pending，正式 FixedEvent 数量不变。
- 开始或结束缺失、两端均缺失、地点/条件未知；不猜两小时默认长度。
- 作业提交、讲座报告完成、考试报名、答辩材料截止被模型错分为 EVENT 时仍被拒绝。
- 模型缩短摘录隐藏截止上下文、同句混杂、其他句子的截止被误当结束时间。
- 不同语句中同时有活动和作业时保留活动，不论前后顺序；多活动证据及时间相互独立。
- 非法字段/编号、无依据时刻、错误类型、标题混用、空结果、批次失败、跨空间/归档/不存在版本在联网前拦截。

2026-10-14 完整后端回归 **515 passed**（本分支基于 X002 的 483 项，加本项 32 项）；Ruff check、format --check、git diff --check 及前端类型检查和构建均通过。保留既有依赖弃用及 integration 标记警告，不将假模型测试当作真实提取准确率。

在 backend 设置独立测试库后执行：

```powershell
uv run --locked pytest tests/test_event_extraction.py -q
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
```

前端运行 `npm run build`。没有执行真实模型准确率评测，也尚未提供活动提取按钮、自动队列任务或外部日历写入。

## 分支与前置

从已同步 develop d813e90 的父分支 issue/#11 创建 issue/#111，只纳入 X001/X002 前置，不把 X003 #359 带入本项。前置 PR #355、#356 已合并，提交前同步父分支 a54782b；本项 PR 的合并目标为 issue/#11。本分支测试基线不包含 X003，因此总测试数不能直接和 X003 的 530 比较。
