# M5 MVP 验收检查表（进行中）

日期：2026-10-18
结论：**未通过完整验收。** 计划功能实现与自动化／浏览器走查已完成；V001、V002、V004、V007 仍缺少真实模型复跑或独立走查，不能把代码完成等同于阶段验收。

## V001 八项 MVP 端到端验收

| # | 验收项 | 证据 | 状态 |
| --- | --- | --- | --- |
| 1 | DDL 答案与引用一致 | Q018 确定性生成器与协议校验；真实模型语义复跑仍待 Q019 | 未完成 |
| 2 | 日期不完整时不补造事实 | X003、Q018 验证；日期精度与待核对字段测试 | 自动化通过，待真实模型复核 |
| 3 | 资料不足时明确说明 | Q011／Q012 固定文案与无候选路径测试 | 自动化通过 |
| 4 | 两周计划符合已知约束并列出未排任务 | `test_plans_api.py`、`test_planning_edges.py`；浏览器走查 | 自动化与走查通过 |
| 5 | 时间不足时展示缺口 | `test_capacity_gap_exposes_adjustment_options`、计划页面容量缺口与调整选项 | 自动化与走查通过 |
| 6 | 调整时间块不修改原始 DDL | `test_move_block_updates_schedule_but_not_task_ddl` | 自动化通过 |
| 7 | 部分解析失败可见 | X008、Q011 覆盖提示测试 | 自动化通过 |
| 8 | 删除后引用状态正确 | Q008、Q014 旧轮次引用复核测试 | 自动化通过 |

阻断项：真实模型语义复核（Q019 #392）尚未补跑；未做独立人工验收。V001 保持开放。

## V002 空间隔离与资料内指令边界

- 计划草案跨工作空间不可见：`test_plan_from_another_workspace_is_not_visible`。
- 意图协议只接受固定枚举与参数白名单；模型返回工具名被 502 拒绝：`test_intent_protocol_returns_boundaries_and_rejects_tools`。
- 资料内指令不会被当作执行授权：X 系列与 Q015 既有测试；本轮未新增真实样本复验。
- 结论：计划部分通过；跨模块完整验收仍待 V002 负责人执行。

## V003 删除与运行中处理的端到端竞态

已有自动化覆盖，本轮未修改相关实现：

```bash
cd backend
uv run --locked pytest tests/test_job_write_guard.py tests/test_material_delete_invalidation.py tests/test_qa_api.py -q
```

相关用例：`test_conversation_keeps_scope_and_marks_deleted_citation_invalid`、`test_job_write_guard.py` 中删除后写入被拒绝的用例、`test_material_delete_invalidation.py` 中来源失效用例。

本轮执行结果：`27 passed`（2026-10-18）。结论：自动化通过；真实队列与独立复验仍按 V003 记录执行。

## V004 页面易用性与无障碍走查

已在独立端口完成真实浏览器走查（登录、生成、接受、锁定、移动、总览）。发现并修复：

- 计划时间按浏览器时区显示，而非工作空间时区；
- ≤760px 导航横向截断；
- 总览重复空状态。

走查快照显示主要区域有角色与可读名称（navigation、region、heading、form label、button 文本）。新增 `frontend/tests/e2e/plan.spec.ts` 覆盖生成、接受、锁定、移动与总览，并修复了因首页改版而失效的旧选择器；隔离端口下 e2e 为 `6 passed, 1 skipped`。未执行键盘全流程、屏幕阅读器和颜色对比度专项检查，V004 保持开放。

## V005 典型样本处理时延与模型成本

- 两周计划为确定性本地计算，不调用模型。
- 本机测试库、单任务样本，连续 5 次 `POST /plans/drafts`：14.2–23.9 ms。
- 计划功能不产生模型费用；问答与嵌入成本仍以 Q001 账本为准。
- 本轮未测量大样本或并发场景，V005 的“典型样本”口径仍需按验收方法补测。

## V006 本地整套启动与故障恢复说明

已更新 [DEVELOPMENT.md](../DEVELOPMENT.md)：补充 M5 迁移、启动新代码前步骤、计划冒烟流程、专项测试命令、输入指纹与 409 语义、当前限制。

本轮按文档在独立端口启动后端（8001，测试库）与前端（5174），完成登录、生成、接受、锁定、移动与总览走查，启动说明可用。未在全新机器或空数据库上从零执行。

## V007 发布说明与候选版本验收

未开始。依赖 V001–V006 完成；当前不应创建 `v0.5.0` 或 `-rc.N` 标签。

## 本轮完成范围

P001–P018 的代码、测试、页面与文档见 [M5-planning.md](M5-planning.md)。M5 里程碑保持 open，等待 V 系列独立验收。
