# K016 三阶段完整回归与 v0.9.0 发布检查

- 任务：#248（父 Issue：#26 E91）
- 日期：2026-10-19
- 结论：代码回归通过，满足 v0.9.0 发布条件；tag 在合并 PR 与必需检查通过后创建。

## 1. 回归范围

| 阶段 | 范围 | 既有验收 | 本轮结果 |
| --- | --- | --- | --- |
| MVP | 学期／课程／任务／固定日程／可用时间、资料导入与定位、核对、带引用问答、两周计划 | M1–M5 记录 | 后端全量通过 |
| 第二阶段 | Word／网页、版本比较与合并、通知变更、依赖任务、局部重排、提醒、导出 | `A022-second-stage-acceptance.md` | 后端全量通过 |
| 第三阶段课程路径 | 培养方案、三类关系、成环／缺项、拓扑顺序、学期辅助 | `C017-course-path-acceptance.md` | 后端全量通过；前端构建通过 |
| 第三阶段外部日历 | 连接授权、草案确认、执行、重试、对账、同步差异、界面 | `M9-external-calendar.md` | 14 个专项测试通过；浏览器闭环通过 |

## 2. 自动化结果

```text
cd backend
uv run --locked pytest -q
1558 passed, 6 skipped, 9 warnings in 79.75s

uv run --locked ruff check .
All checks passed!

uv run --locked ruff format --check .
342 files already formatted
```

```text
cd frontend
npm run build
vue-tsc --noEmit 通过；vite build 通过（88 modules transformed）
```

专项：

```text
uv run --locked pytest tests/test_calendar_connections.py tests/test_calendar_execution.py -q
14 passed
```

## 3. 时间敏感用例修正

全量回归发现 7 个既有排期用例使用“今天”作为计划区间起点；在当天较晚运行时会因剩余可用时间不足而失败，与 M9 改动无关。已为以下用例启用既有 `fixed_planning_clock` 固定时钟，保证任意时刻运行结果稳定：

- `test_m7_dependency_acceptance.py`：锁定冲突、耗时增加、已完成前置；
- `test_planning_reschedule.py`：保留锁定块、范围外保留、全部锁定不重复；
- `test_planning_adjustments.py`：进度变化只生成建议、不覆盖当前计划；
- `test_subtasks.py`：父任务耗时不重复计入。

修正只作用于测试，不改变排期实现或业务规则。

## 4. 浏览器闭环

使用本地模拟供应商与 Vite 开发服务器完成：

1. 登录并进入 `/calendar`；
2. 发起授权，回调建立连接；页面显示账户、目标日历和到期时间；
3. 勾选两个任务生成预览；预览阶段外部事件数为 0；
4. 确认后显示 15 分钟有效期；执行后两项均显示“已写入”与外部事件 ID；
5. 刷新页面后连接、执行结果与内部任务仍存在。

截图与步骤记录在 [M9 记录](M9-external-calendar.md) 的浏览器验证一节；未保存账户凭据。

## 5. 发布条件

- [x] E90（#25）与 E91（#26）的叶子交付齐全，代码合并到 `main`。
- [x] 后端全量测试、Ruff 检查与格式检查通过。
- [x] 前端类型检查与构建通过。
- [x] 三阶段验收均可定位到实测证据。
- [x] 已知限制写入文档，未把模拟供应商结果当作真实线上同步。
- [ ] 创建 `v0.9.0` tag 与 Release（仅在上条 PR 合并、必需检查通过后执行，不移动已发布 tag）。

## 6. 发布说明摘要

- 新增外部日历：Google Calendar API v3 适配器、最小权限授权、连接撤销、逐项确认、写入与执行记录。
- 新增安全边界：令牌后端加密、OAuth state 一次性、确认指纹绑定内容与目标日历、过期确认拒绝执行。
- 新增可靠性：逐项成功／失败／待核实状态、幂等键、超时对账、只重试失败项、成功项不重复创建。
- 新增变更维护：任务与计划变化生成更新／删除草案，撤销连接后不再执行外部写入。
- 新增页面：`/calendar` 连接、预览、确认、执行结果、失败重试与差异检查。

## 7. 已知限制

- 真实 Google Cloud OAuth 客户端与测试账户尚未提供；真实授权、写入、ETag 冲突与撤销未验证。
- 本地模拟供应商只用于显式配置的测试与演示，事件不持久化。
- 首版只写单次事件，不支持参与者、提醒、重复规则、附件或描述原文。
- 撤销连接不删除已写入的外部日历或事件。
- 无后台自动同步或推送；同步差异需用户主动检查并重新确认。
- 计划清单 CSV、真实日历客户端导入、后台定时提醒等既有缺口不受本里程碑影响。
