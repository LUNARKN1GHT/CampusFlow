# A022 第二阶段端到端验收与 v0.7.0 发布记录

作者：李亦凡；任务 #221；父任务 #22（E72）。前置 #172（N009）、#178（N019）、#212（A009）、#216（A016）、#217（A021）。

- 验收分支：`issue/#221`，起点 `59fdea7`（A021 并入 E72 后）。
- 发布基线：E72 集成分支 `issue/#22` 合并进 `main` 后的提交，tag 为 `v0.7.0`。
- 环境：本机 PostgreSQL 17 测试库；确定性生成器，不调用外部模型。

## 1. 串联范围

| 业务链路 | 验收证据 |
| --- | --- |
| Word／网页导入与定位 | `tests/test_n009_extended_input_acceptance.py`（3 项）、`docs/verification/N009-extended-input-acceptance.md` |
| 通知冲突与变更历史 | `tests/test_n019_change_acceptance.py`（4 项）、`docs/verification/N019-change-acceptance.md` |
| 依赖排期与局部重排 | `tests/test_m7_dependency_acceptance.py`（5 项）、`docs/verification/A009-dependency-acceptance.md` |
| 应用内提醒 | `tests/test_m7_reminder_acceptance.py`（5 项）、`docs/verification/A016-reminder-acceptance.md` |
| 清单与日历导出 | `tests/test_exports.py`（17 项）、`tests/test_export_interop.py`（6 项）、`docs/verification/A018`–`A021` |
| 导出界面 | `frontend/src/views/ExportView.vue`、`docs/verification/A020-export-ui.md`、`npm run build` |

## 2. 执行结果

```text
backend$ uv run --locked pytest -q \
  tests/test_n009_extended_input_acceptance.py \
  tests/test_n019_change_acceptance.py \
  tests/test_m7_dependency_acceptance.py \
  tests/test_m7_reminder_acceptance.py \
  tests/test_exports.py tests/test_export_interop.py
40 passed

backend$ uv run --locked pytest -q
1494 passed, 6 skipped

backend$ uv run --locked ruff check .
All checks passed!

backend$ uv run --locked ruff format --check .
... clean

frontend$ npm run build
vue-tsc --noEmit && vite build  ✓
```

## 3. 验收条件

### 3.1 通知冲突验收通过且无静默覆盖

- 较晚导入但发布更早的旧通知不会覆盖当前要求；冲突未被显式确认时不改变候选核对状态，接受请求返回 400。
- 缺少替代声明、范围未知或发布时间缺失时进入待核对（`requires_review`），不自动推荐。
- 跨学期／课程同名事项不进入重复建议，也不能通过合并预览强制合并。
- 接受后保留旧／新版本标识、原因和逐字段证据快照，可追溯。
- 接受通知变更只触发计划调整建议或提醒重算，不改写任务 DDL；A009／A016 的「通知变更」用例断言不存在静默覆盖或双份待发送提醒。

### 3.2 发布候选证据与已知限制

- 后端全量 `1494 passed, 6 skipped`；M7 目标套件 `40 passed`；前端类型检查与生产构建通过。
- 导出覆盖显式任务与当前已接受计划，日期精度、UTC 换算、跨日、每周固定日程、UID 稳定与隐私字段均有测试。
- 已知限制见 §4；v0.7.0 的功能状态以本记录和对应验证文档为准。

## 4. 已知限制

- **真实日历客户端导入未执行**：A021 以属性级解析验证 ICS 语义，未在 Calendar.app／Outlook 等客户端完成导入展示。
- **无后台定时提醒进程**：提醒在读取与业务写入时同步；服务未运行期间不主动推送。
- **计划清单 CSV 未提供**：计划模式当前只导出 ICS，界面禁用 CSV。
- **固定日程来源提示不导出**：`FixedEventOut` 未暴露来源核对状态，不能声称其引用可验证。
- **接受新计划可能产生重复日历事件**：计划块 ID 变化导致 UID 变化；界面提示先删除旧事件。
- **外部日历写入未接入**：本阶段只生成本机文件，不同步到任何外部账号；属于 M9。
- **通知更新仍靠用户导入**：不自动读取群聊或后台抓取教务通知。
- **模型数据边界**：在供应方条款澄清前，不把真实校务资料发往外部模型；验收使用合成或确定性样本。
- **单用户本机会话**：当前登录为进程内随机会话，服务重启失效，不等同生产身份系统。

## 5. 发布步骤与结果

1. 叶子 PR #436（A018）、#438（A019）、#439（A020）、#440（A021）并入 `issue/#22`；A017 文档 PR #429 已并入。
2. E72 集成分支 PR 合并进 `main`（本记录随 A022 PR 并入后执行）。
3. 在合并后的 `main` 提交创建 `v0.7.0` tag 与 GitHub Release，说明第二阶段范围与上述已知限制。
4. 关闭 A017–A022 叶子 Issue 与 E72 父 Issue；M7 里程碑按 GitHub 记录关闭。

> tag 只在目标提交合并且验收通过后创建；已发布 tag 不移动。
