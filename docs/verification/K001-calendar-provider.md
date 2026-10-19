# K001 首个外部日历供应商与连接范围

- 任务：#233（父 Issue：#25 E90）
- 结论日期：2026-10-19
- 结论：首个供应商选择 **Google Calendar API v3**，连接范围限定为 **应用自建的专用次级日历**，首版支持新增、修改、删除单次事件。

## 1. 评估口径

只比较能够完成以下闭环的方案：

1. 用户显式授权后，后端能代表用户写入一个明确指定的日历。
2. 支持新增、修改、删除，并能取得外部事件标识与版本（ETag）。
3. 能校验授权失效、用户撤销和权限不足。
4. 能按任务时区写入带时刻事件，按日期写入全天事件。
5. 重试时不重复创建，并能对超时后的未知结果进行对账。
6. 有可核对的成本、配额和隐私边界。

团队实际可用账户是硬条件。当前仓库环境没有 Google Cloud 项目、OAuth 客户端或测试账户，因此真实账户验证无法在本轮执行；代码级验收使用确定性的模拟供应商，**真实 Google 账户写入标为未验证**，不把模拟结果当作线上结论。

## 2. 候选对比

| 方案 | 授权与撤销 | 新增/修改/删除 | 幂等与对账 | 主要限制 | 结论 |
| --- | --- | --- | --- | --- | --- |
| Google Calendar API v3 | OAuth 2.0 授权码 + refresh token；撤销令牌接口 | Events insert / patch / delete | private extended properties + ETag / If-Match | 需要 Google Cloud OAuth 客户端；测试账户待提供 | **选定** |
| Microsoft Graph（Outlook 日历） | OAuth 2.0 + Microsoft identity platform | create / patch / delete | 自定义扩展 + ETag | 团队账户与租户权限未确认 | 备选 |
| CalDAV（自建 Radicale 等） | HTTP Basic / 应用专用密码 | PUT / DELETE | UID + `If-Match` | 无标准 OAuth 回调与撤销流程，与 K003 要求不匹配 | 不选 |
| Apple Calendar | 无公开的服务端写入 API | 不适用 | 不适用 | 只能经由客户端或 CalDAV | 不选 |

## 3. 选定方案与连接范围

### 3.1 授权范围

- 使用 OAuth 2.0 授权码流程，后端保存 access token、refresh token、过期时间、账户标识与目标日历 ID。
- 仅申请 `https://www.googleapis.com/auth/calendar.app.created`：
  - 允许应用创建一个专用次级日历，并管理该日历中由应用创建的事件；
  - 不读取或修改用户主日历及其它日历中的事件；
  - 不申请通讯录、邮件或 Drive 权限。
- 首次连接时由后端创建名为 `CampusFlow` 的次级日历（或复用已记录的同名应用日历），把 `calendar_id` 与连接绑定。用户撤销连接后不自动删除该日历，避免误删用户仍在使用的事件。

### 3.2 首版事件范围

首版只写单次事件，字段限于：

- 标题（任务或计划块标题）；
- 开始、结束时间；只有日期的任务写全天事件；
- 时区（带时刻事件使用工作空间或计划时区）；
- 地点（仅当内部对象显式提供）；
- 应用写入键（`extendedProperties.private.campusflow_operation_key`）与来源标识。

首版不写：描述／原文／教师联系方式、参与者与邀请、会议链接、提醒（VALARM）、重复规则、附件。固定日程的每周重复展开由用户在应用内选择后作为单次事件写入，不写 RRULE。

### 3.3 修改、删除与幂等

- 新增：先按 operation key 查询；不存在才创建。创建请求携带 operation key。
- 修改：使用 `PATCH` + `If-Match: <etag>`，只修改确认范围内的字段；ETag 不匹配时报告冲突，不覆盖用户后来在外部日历中的修改。
- 删除：使用 `DELETE` + `If-Match`；目标已不存在时记录实际结果（`not_found`），不重复删除。
- 超时或连接中断：操作进入 `unknown`，通过 operation key 查询对账；确认已创建则补记外部事件 ID，确认不存在才允许安全重试；无法确认时保持待核实，不显示为已同步。

## 4. 成本、配额与安全

- Calendar API 不按事件写入收费；配额以 Google Cloud 控制台当前策略为准，文档不写死数值。
- OAuth 客户端密钥与 refresh token 只保存在后端；数据库中的令牌使用 Fernet 加密，密钥来自 `CAMPUSFLOW_CALENDAR_TOKEN_KEY`。
- 接口与日志不返回 access token、refresh token、授权码或完整外部响应。
- 用户撤销连接后立即停止执行待写入操作；已写入外部的事件不会因撤销而被删除。

## 5. 外部前置条件与未验证项

- 需要项目组提供 Google Cloud 项目、OAuth 同意屏幕配置、客户端 ID / Secret、以及可用于测试的 Google 账户。
- 本轮无法执行真实 OAuth 授权、真实账户写入和真实撤销；K015 使用模拟供应商覆盖成功、失败、部分成功、响应丢失和撤销路径。
- 真实账户验证完成前，不得在对外材料中宣称“已完成 Google 日历线上同步”。

## 6. 验收对应

- [x] 供应商与连接范围已明确，并给出未选方案的理由。
- [x] 首版新增、修改、删除范围已限定。
- [x] 真实账户缺口已记录，产物不冒充线上验证。
- [ ] 真实 Google 测试账户验证（外部前置条件，待项目组提供后补跑）。
