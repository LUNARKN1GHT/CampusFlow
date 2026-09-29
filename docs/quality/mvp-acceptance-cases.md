# MVP 八项业务验收用例

本表把 README 的八项 MVP 验收场景固定为可重复执行的输入、步骤、预期和证据位置。它定义“怎样判断通过”，不表示对应功能已经实现或验收通过。执行记录必须关联确切提交，并逐项填写 `passed`、`failed` 或 `blocked`；不得用“部分完成”代替失败条件。

机器可读的完整步骤和结果字段见 [mvp-acceptance-cases.json](mvp-acceptance-cases.json)。运行 `python3 scripts/validate_mvp_acceptance_cases.py` 可检查八项完整性、样本登记和证据路径格式。

| 用例 | README 场景 | 输入 | 独立通过条件 | 证据目录 |
| --- | --- | --- | --- | --- |
| MVP-01 | 从资料回答作业 DDL | `CF-PDF-001` | 事项、日期、教学班正确；引用第 1 页对应字段 | `evidence/mvp/<run-id>/MVP-01/` |
| MVP-02 | 截图日期不完整 | `CF-IMG-004`、`CF-TXT-002` | 缺失年份/时刻可见，未生成已确认 DDL | `evidence/mvp/<run-id>/MVP-02/` |
| MVP-03 | 用户询问未覆盖课程 | 仅导入数据结构资料后询问离散数学 | 明确“现有资料中未发现”，不声称无作业 | `evidence/mvp/<run-id>/MVP-03/` |
| MVP-04 | 生成未来两周计划 | 固定日程、可用时间及三项任务 | 已排项满足约束；未排项逐项列出原因 | `evidence/mvp/<run-id>/MVP-04/` |
| MVP-05 | 工作量超出可用时间 | 10 小时容量与 14 小时任务 | 显示 4 小时以上缺口及调整选项，不隐藏任务 | `evidence/mvp/<run-id>/MVP-05/` |
| MVP-06 | 修改学习时间块 | 已确认 DDL 与已接受时间块 | 只改变个人时间块，原始 DDL 值和来源不变 | `evidence/mvp/<run-id>/MVP-06/` |
| MVP-07 | 文件部分识别失败 | `CF-IMG-002`、`CF-IMG-003` | 已读结果可见且未覆盖页/区域可定位，不显示全部成功 | `evidence/mvp/<run-id>/MVP-07/` |
| MVP-08 | 删除被引用资料 | 被答案和任务引用的 `CF-PDF-001` | 删除前展示影响；删除后引用失效且任务不静默消失 | `evidence/mvp/<run-id>/MVP-08/` |

## 执行规则

1. 每次运行创建唯一 `<run-id>`，推荐使用 `YYYYMMDD-短提交号`；目录中保存输入快照、操作截图或 API 响应、实际结果和结果记录。
2. 每项用例从干净工作空间开始，除用例明确复用的对象外不沿用前一项状态。
3. `passed` 要求全部预期结果成立且没有任何失败条件；环境或前置功能不可用时记为 `blocked` 并写明原因，不得记为通过。
4. 引用证据记录文件、页码/段落/区域和版本；界面截图只证明展示结果，不能替代底层值或来源核对。
5. 删除、计划接受等敏感动作只在测试工作空间中执行，并保留操作前后状态。真实资料不得作为本表的默认输入。

## 结果记录

每项 `result.json` 至少包含：`case_id`、`commit`、`environment`、`started_at`、`finished_at`、`status`、`actual_results`、`evidence_files`、`defect_links`、`executed_by`。当状态为 `failed` 或 `blocked` 时，必须填写 `notes`；未运行的步骤不能写成通过。
