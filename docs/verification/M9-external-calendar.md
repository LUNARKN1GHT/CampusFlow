# M9 外部日历：实现与验收记录

- 里程碑：M9 · 外部日历与完整流程验收
- 父 Issue：#25（E90 授权与操作确认）、#26（E91 执行可靠性与完整流程验收）
- 日期：2026-10-19
- 供应商选择：见 [K001](K001-calendar-provider.md)

## 1. 交付范围

M9 新增外部日历连接、授权、写入草案、逐项确认、执行记录、失败重试和同步差异：

| 任务 | 交付物 | 证据 |
| --- | --- | --- |
| K001 | 选定 Google Calendar API v3，最小权限 `calendar.app.created` | [K001](K001-calendar-provider.md) |
| K002 | 连接与凭据存储；令牌后端加密；空间隔离 | `calendar_connections`、`infrastructure/calendar/tokens.py`、`test_calendar_connections.py` |
| K003 | OAuth state、错误回调、撤销与过期 | `calendar_oauth_states`、`complete_authorization`、`revoke_connection` |
| K004 | 外部操作草案与确认版本身份 | `calendar_drafts`、内容指纹与确认指纹 |
| K005 | 外部写入影响预览与逐项范围 | `GET /calendar/drafts/{id}`、勾选条目确认 |
| K006 | 执行前校验连接、目标日历、内部内容与有效期 | `_validate_confirmation`、`_validate_items_are_current` |
| K007 | 连接与逐项确认页面 | `frontend/src/views/CalendarView.vue`、`/calendar` |
| K008 | 执行记录、幂等键与错误类别 | `calendar_operations`、唯一 `idempotency_key` |
| K009 | 新增事件适配器 | `infrastructure/calendar/google.py`、`local.py` |
| K010 | 修改与删除适配器 | ETag/If-Match、`not_found` 实际结果 |
| K011 | 超时未知结果与安全重试 | `CalendarUnknownResult`、`find_event` 对账、`/reconcile` |
| K012 | 批量逐项结果与只重试失败项 | `execute` / `retry` 返回逐项状态 |
| K013 | 通知与计划变化引起的同步差异 | `GET /calendar/differences`、`POST /calendar/differences/draft` |
| K014 | 同步结果、失败重试与撤销提示界面 | 执行结果区、未知结果核对按钮、撤销入口 |
| K015 | 成功、失败、部分成功、响应丢失、撤销验收 | 14 个日历集成测试 |
| K016 | 三阶段回归与发布检查 | [K016 记录](K016-third-stage-acceptance.md) |

## 2. 数据与安全边界

- 新表：`calendar_connections`、`calendar_oauth_states`、`calendar_drafts`、`calendar_draft_items`、`calendar_operations`、`calendar_event_links`。
- 数据库只保存 access token / refresh token 的 Fernet 密文；密钥来自 `CAMPUSFLOW_CALENDAR_TOKEN_KEY`。接口响应、日志和异常文案都不包含令牌或授权码。
- OAuth state 只保存 SHA-256 哈希，绑定工作空间、回调地址，10 分钟内有效且只能使用一次。
- 连接、草案、操作、映射全部按 `workspace_id` 过滤；跨空间访问返回 404。
- 外部事件映射保留来源类型、来源编号、外部事件 ID、ETag、内容指纹与快照；删除或外部缺失后保留状态，不静默丢失。

## 3. 确认与执行不变量

- 生成预览只读内部数据和数据库，不调用供应商。
- 草案内容指纹由连接、目标日历和全部条目的内容指纹组成；确认指纹额外绑定选中条目与版本号。
- 执行前校验：连接仍为 `active`、目标日历未变、草案内容未被改写、内部任务/计划与草案一致、确认未过期。
- 撤销连接会取消待写入草案，执行返回 409；已写入外部的事件不会被删除，内部任务保留。
- 供应商明确失败逐项记录；超时或 5xx 记录为 `unknown`，必须通过幂等键对账后才能安全重试。
- 已成功条目不会重复执行；重试仅覆盖 `failed` / `unknown` / `pending` 条目。
- 只有实际成功或对账确认已存在的事件才写入映射并显示为“已写入”。

## 4. API

```text
GET    /api/v1/calendar/connections
GET    /api/v1/calendar/oauth/start
GET    /api/v1/calendar/oauth/callback
DELETE /api/v1/calendar/connections/{id}
POST   /api/v1/calendar/drafts
GET    /api/v1/calendar/drafts/{id}
POST   /api/v1/calendar/drafts/{id}/confirm
POST   /api/v1/calendar/drafts/{id}/execute
POST   /api/v1/calendar/drafts/{id}/retry
GET    /api/v1/calendar/operations?draft_id=
POST   /api/v1/calendar/operations/{id}/reconcile
GET    /api/v1/calendar/differences?connection_id=
POST   /api/v1/calendar/differences/draft
```

## 5. 验证结果

### 5.1 自动化测试

```text
cd backend
uv run --locked pytest tests/test_calendar_connections.py tests/test_calendar_execution.py -q
14 passed
```

覆盖：

- OAuth 成功、错误回调、state 重放与过期、错误回调不建立连接；
- 连接响应与数据库密文不含明文令牌；
- 预览不产生外部事件；未确认、错误确认指纹、过期确认不能执行；
- 内部内容变化、目标日历变化使旧确认失效；
- 带时刻任务与仅日期任务（全天）；
- 部分成功、只重试失败项、成功项不重复；
- 写入成功但响应丢失、手工对账与重试不重复建项；
- 更新目标不存在、删除目标已不存在、内部任务保留；
- 任务变化生成更新草案、取消生成删除草案；
- 计划模式只写已接受计划的时间块；
- 撤销连接阻断待写入操作并保留内部任务；
- 跨工作空间连接不可访问。

### 5.2 浏览器验证

使用 `CAMPUSFLOW_CALENDAR_PROVIDER=local` 启动后端，Vite 开发服务器访问 `/calendar`：

1. 登录后进入“外部日历”，点击“连接外部日历”，完成本地模拟授权，页面显示账户、日历与已连接状态。
2. 勾选两个任务生成预览，逐条显示操作类型、来源、时间与时区；此时未创建外部事件。
3. 点击确认后状态变为“已确认”，页面显示确认有效期；点击执行后逐项显示“已写入”和外部事件 ID。
4. 刷新页面后连接仍存在；执行结果与内部任务均保留。

## 6. 已知限制

- **真实 Google 账户未验证**：当前环境没有 Google Cloud OAuth 客户端和测试账户，K001/K015 使用确定性本地模拟供应商完成代码级验收。真实 OAuth、真实事件写入、真实撤销与 ETag 冲突需在提供账户后补跑，不能把本地模拟结果当作线上结论。
- 本地模拟供应商仅用于显式配置 `CAMPUSFLOW_CALENDAR_PROVIDER=local` 的自动化测试和演示；事件保存在进程内，重启丢失。
- 首版只写单次事件，不支持参与者、提醒、重复规则、附件或会议链接；仅日期任务写全天事件。
- 撤销连接不删除应用已创建的外部日历或事件。
- 没有后台自动同步或推送；同步差异需用户主动检查并重新确认。
- 计划模式只接受当前已接受计划；草案不会被自动接受。
