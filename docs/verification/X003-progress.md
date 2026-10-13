# X003 续接记录（未完成）

日期：2026-10-13。作者：金嘉睿（24151735）。任务：#110，父任务：#11。用户允许本轮在额度有限时保存可验证的部分成果，本记录不代表 X003 验收通过。

## 当前实现

- `backend/src/campusflow/application/task_extraction.py` 定义任务提取契约：title、submission_method、deliverable、due_text 和 estimated_minutes。
- 前四项要求保留对应证据中的连续原文，防止模型把真实引用附在补写的日期或提交方式上；X002 继续校验片段编号和摘录真实性。
- 耗时只允许 suggestion 出处、正整数分钟和估计说明，程序补充可编辑建议标识。
- 缺失字段保留 null、missing_reason 和对应 ReviewReason；不会因模型未返回 review_reasons 而隐藏缺失项。
- `extract_task_drafts` 沿用 X002 的空间/版本检查，仅返回草稿，暂不保存、不创建正式任务、不接 API 或队列。
- 专项测试 `backend/tests/test_task_extraction.py`：12 passed。覆盖字段提取、缺失标记、伪造日期/提交方式、无效耗时、批量失败、跨空间拦截与无写入副作用。使用真实测试数据库和假模型，未调用真实智谱。

本轮完整后端回归 **495 passed**；Ruff check、format --check、前端类型检查和构建、git diff --check 均通过。测试通过仅验证已实现部分，不覆盖下列未完成的验收项。

## 下次从这里继续

1. 完成年份、时刻缺失的专项检查和标记。目前 due_text 能原样保留“下周五提交”，但尚未自动生成 missing_year / missing_time；这是 X003 明确未满足的验收项，不能关闭 Issue。
2. 用 `samples/synthetic/b012/missing-year-time.txt`、`relative-date.txt` 及其 expected.json 对齐预期；再覆盖 B010 中有明确截止时间的任务。缺年份不默认补今年，缺时刻不补 00:00 或 23:59，相对日期换算属于 X006。
3. 明确日期组件的字段契约与 X006 衔接，补充缺失/完整时间/混合通知的测试；不能仅靠提示词保证日期不补造。
4. 检查模型原文提取对条件、跨段证据和多项任务的覆盖，补充必要测试；真实准确率留 X010，假模型测试不算准确率评测。
5. 完成后再更新正式验收说明和 PR 状态。保存仍需显式使用 X001 的原子保存入口；API/Worker 自动提取和 R 系列交互不在当前成果中。

## 分支与前置

工作目录 `CampusFlow-x001`，当前分支 `issue/#110`，目标父分支 `issue/#11`。X001 PR #355 与 X002 PR #356 尚未合并；本分支仅为继续开发纳入它们的提交，未经作者自行批准或合并。审查本项时看本项独立提交，完整 PR 差异会暂时包含前置代码。下次先核对前置审核意见及父分支更新，禁止强推覆盖队友工作。
