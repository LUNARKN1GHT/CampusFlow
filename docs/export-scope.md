# 清单与日历导出的范围与字段（A017）

- Issue：#205（A017）· 父 Issue #22（E72）· M7
- 依据：README §4.10、§7；AGENTS.md 的来源、任务时间与数据边界规则
- 状态：范围与字段定义完成。A018 实现任务清单，A019 实现 ICS，A020 实现预览与下载界面，A021 验证日期与时区互操作。

## 1. 两种导出模式

导出范围必须由用户显式给出，两种模式互斥；格式（CSV／ICS）与模式相互独立。

| 模式 | 范围来源 | 包含 | 不包含 |
| --- | --- | --- | --- |
| `tasks` 所选任务 | 请求携带非空 `task_ids`，逐项属于当前工作空间 | 用户勾选的任务；可按学期／课程筛选后勾选，但筛选结果不自动成为范围 | 未勾选任务、整空间任务、候选事项、草稿计划 |
| `plan` 已接受计划 | 请求携带 `plan_id`；服务端校验该草案 `status=accepted`，且仍是其学期当前已接受计划 | 该草案 `blocks` 中的 `kind=task` 时间块；清单可含计划内未排入任务；可选固定日程 | 草案（`status=draft`）、被替代计划（`superseded`）、未排入时间块、下一版计划 |

规则：

- `task_ids` 为空、缺失或包含其他空间的任务时拒绝，不回退为“全部任务”。不提供整空间导出入口。
- 计划模式不自动接受草案；没有已接受计划时返回明确错误，由用户先在计划页接受。
- 计划模式只导出 `range_start` 至 `range_end` 内的时间块；固定日程默认不导出（见 §3.3）。
- 归档学期不删除数据：显式选择归档学期的任务或已接受计划可以导出，预览标注“历史学期”，不改变归档语义。
- 导出是本地文件下载，不写入外部日历、不发送消息，也不产生外部操作记录。

## 2. 排除与降级项

| 原因代码 | 内容 | 处理 |
| --- | --- | --- |
| `candidate_unconfirmed` | 待核对、已忽略、已失效候选及候选字段 | 全部排除；只有已转换的正式任务／日程可导出 |
| `invalid_source_claim` | 来源已删除、归档或引用不可定位 | 任务保留并标注 `source_state=invalidated`；来源提示整体省略，不输出旧标题／定位 |
| `raw_material_text` | 资料原文、片段、引用摘录、截图文字 | 从不导出；来源提示只到标题、版本号和定位 |
| `cancelled_task` | `progress=cancelled` 的任务及其时间块 | 两种模式均排除；预览计数并警告 |
| `buffer_block` | `PlanBlock.kind=buffer` 的提交缓冲 | 排除；缓冲不是用户活动 |
| `orphan_block` | `task_id` 为空的时间块 | 排除 |
| `unscheduled_task` | 已接受计划中未排入的任务 | 清单保留并标 `plan_status=unscheduled`；ICS 无法定位，排除并警告 |
| `no_due_date` | `due_date` 为空的任务 | 清单保留，`due_precision=none`；ICS 排除，不补造日期 |
| `fixed_event_disabled` | 用户未勾选包含固定日程 | 计划模式默认排除；预览说明可勾选 |
| `fixed_event_out_of_range` | 固定日程发生区间不与计划范围重叠 | 排除 |

同一对象只按首要原因计数一次。跨空间或不存在的 ID 返回 404／403，不静默跳过，也不回显其他空间名称。

## 3. 字段定义

### 3.1 任务清单 CSV（两种模式共用基础列）

CSV 使用 UTF-8 带 BOM、CRLF、RFC 4180 引号规则；表头固定，空值留空而不写 `null`。日期 `YYYY-MM-DD`，时刻 `HH:MM`（有秒则 `HH:MM:SS`）。

| 列名 | 来源字段 | 说明 |
| --- | --- | --- |
| `export_version` | 常量 | 当前 `1`，字段变化时递增 |
| `task_id` | `Task.id` | 稳定标识；不含工作空间名称 |
| `course_id` | `Task.course_id` | 未关联课程时为空 |
| `course_name` | `Course.name` | 仅课程名；不导出 `Course.teacher`、`Course.class_name` |
| `title` | `Task.title` | 按存储内容导出，不截断；特殊字符由 CSV 引号处理 |
| `due_date` | `Task.due_date` | 没有截止时为空 |
| `due_time` | `Task.due_time` | 仅 `due_time` 有值才写；不补 `00:00`／`23:59` |
| `due_precision` | 计算 | `date`（有日期无时刻）、`datetime`（两者都有）、`none`（无日期） |
| `due_source` | `Task.due_source` | `user`／`document`／`inferred` |
| `target_date` | `Task.target_date` | 个人目标日期，与正式 DDL 分列 |
| `priority` | `Task.priority` | `low`／`medium`／`high` |
| `progress` | `Task.progress` | `not_started`／`in_progress`／`blocked`／`done`／`cancelled` |
| `estimated_minutes` | `Task.estimated_minutes` | 未知为空；不清零 |
| `remaining_minutes` | `Task.remaining_minutes` | 未知为空 |
| `source_state` | `TaskOut.source_state` | `available`／`invalidated`／`none` |
| `source_title` | `Material.title` | 仅在 `include_source_hints=true` 且来源仍可核对时填写 |
| `source_version_no` | `MaterialVersion.version_no` | 同上；不写版本备注 |
| `source_locator` | `SourceCitationOut.locator_type` + `locator_value` | 形如 `page:12`、`paragraph:4`；不写 `quote` |

计划模式追加列：

| 列名 | 来源 | 说明 |
| --- | --- | --- |
| `plan_id` | `PlanDraftOut.id` | 已接受计划标识 |
| `plan_version` | `PlanDraftOut.version` | 导出所依据的版本 |
| `plan_timezone` | `PlanDraftOut.timezone` | 计划生成时使用的时区 |
| `plan_status` | 计算 | `scheduled`／`unscheduled` |
| `block_count` | 该任务在计划中的 `kind=task` 块数 | 未排入为 `0` |
| `scheduled_minutes` | 各块 `minutes` 之和 | 未排入为 `0` |
| `first_block_start` | 最早块 `starts_at` | ISO 8601 带偏移；未排入为空 |
| `last_block_end` | 最晚块 `ends_at` | 同上 |
| `locked_block_count` | `locked=true` 的块数 | 只表示用户锁定，不改变时间 |

不导出 `Task.description`、`Task.due_note`、`PlanBlock.reason`、`PlanBlock.constraint_source`、候选字段、资料发布方／链接及任何原文摘录。排序固定为截止日期（空值在后）、优先级、`task_id`；计划模式先按 `first_block_start`，再按 `task_id`。

### 3.2 ICS 通用约定

- `VERSION:2.0`、`CALSCALE:GREGORIAN`、`METHOD:PUBLISH`、`CLASS:PRIVATE`；UTF-8、CRLF、75 字节折行，文本转义 `\\`、`\,`、`\;`、`\n`。
- 时间点统一写 UTC（`...Z`）；只有日期的截止写 `DTSTART;VALUE=DATE`，`DTEND` 为次日（不包含），绝不补具体时刻。`X-WR-TIMEZONE` 只记录来源时区，不替代 UTC。
- 默认不生成 `VALARM`；不写 `DESCRIPTION`。`SUMMARY` 只用任务／块标题，`LOCATION` 只在固定日程勾选时写入 `FixedEvent.location`。
- 每个组件带 `DTSTAMP`（导出时间）和 `UID`；允许重复导出时其余字段保持确定性。

### 3.3 各模式组件

| 模式 | 组件 | UID | 主要属性 |
| --- | --- | --- | --- |
| `tasks` | 每个所选任务一个 `VEVENT` | `campusflow-task-{Task.id}-due@campusflow.local` | `DTSTART`／`DTEND` 按 §3.4；`SUMMARY=Task.title`；`STATUS:CONFIRMED`；`TRANSP:TRANSPARENT` |
| `plan` | 每个 `kind=task` 块一个 `VEVENT` | `campusflow-plan-{draft_id}-block-{block_id}@campusflow.local` | `DTSTART`／`DTEND` 为块的实际起止（UTC）；`SUMMARY=PlanBlock.title`；`STATUS:CONFIRMED`；`TRANSP:OPAQUE`；`SEQUENCE=PlanDraftOut.version` |
| `plan`（可选） | 每条固定日程发生一个 `VEVENT` | 非重复：`campusflow-fixed-{FixedEvent.id}@campusflow.local`；每周发生：追加 `-{UTC 开始时间}` | `SUMMARY=FixedEvent.title`；`LOCATION=FixedEvent.location`；`DTSTART`／`DTEND` 原样换算 UTC；展开后不再写 `RRULE` |

自定义提示属性（均可省略，不参与语义判断）：`X-CAMPUSFLOW-KIND`（`task-due`／`plan-block`／`fixed-event`）、`X-CAMPUSFLOW-TASK-ID`、`X-CAMPUSFLOW-PLAN-ID`、`X-CAMPUSFLOW-BLOCK-ID`、`X-CAMPUSFLOW-DRAFT-VERSION`、`X-CAMPUSFLOW-LOCKED`、`X-CAMPUSFLOW-DUE-PRECISION`、`X-CAMPUSFLOW-DUE-SOURCE`、`X-CAMPUSFLOW-PROGRESS`、`X-CAMPUSFLOW-PRIORITY`、`X-CAMPUSFLOW-SOURCE-STATE`、`X-CAMPUSFLOW-SOURCE-HINT`（标题 v版本 · 定位）、`X-CAMPUSFLOW-FIXED-EVENT-ID`、`X-CAMPUSFLOW-COURSE`（课程名）。提示属性不得写入原文引文。固定日程组件暂不写来源提示：`FixedEventOut` 未暴露来源状态，不能在没有核对的情况下声称引用可验证。

### 3.4 截止时间精度

| `due_date` | `due_time` | ICS | 说明 |
| --- | --- | --- | --- |
| 有 | 无 | 全天事件：`DTSTART;VALUE=DATE:<due_date>`，`DTEND;VALUE=DATE:<due_date+1>` | 不生成 00:00／23:59；`X-CAMPUSFLOW-DUE-PRECISION:DATE` |
| 有 | 有 | `DTSTART:<UTC 时间>`，`DTEND:<UTC+15 分钟>` | 15 分钟仅是日历显示窗口，不表示耗时，也不延长截止；`X-CAMPUSFLOW-DUE-PRECISION:DATETIME` |
| 无 | — | 不导出 | 清单中 `due_precision=none`，预览警告 |

`Task.target_date` 只是个人安排目标，不写入 ICS，避免客户端把个人目标当成正式截止。

## 4. 预览契约

预览与下载使用同一范围快照；建议接口 `POST /api/v1/exports/preview` 与 `POST /api/v1/exports`（A018／A019 实现，A020 调用）。

请求：

```json
{
  "mode": "tasks",
  "format": "csv",
  "task_ids": [12, 18],
  "plan_id": null,
  "options": {"include_source_hints": true, "include_fixed_events": false}
}
```

计划模式必须给 `plan_id`，`task_ids` 必须为空；任务模式反之。`include_fixed_events` 只对计划模式的 ICS 生效。

预览响应至少包含：

| 字段 | 内容 |
| --- | --- |
| `mode`／`format` | 当前选择 |
| `scope` | 任务 ID 列表，或 `plan_id`、`plan_version`、`semester_id`、`range_start`、`range_end` |
| `timezone` | 任务模式为工作空间时区；计划模式为 `PlanDraftOut.timezone` |
| `item_count` | 源对象数；`row_count`（CSV）或 `event_count`（ICS） |
| `date_range` | 最早／最晚日期（含 `has_date_only`），无日期时为空 |
| `counts` | `date_only`、`timed`、`no_due`、`scheduled`、`unscheduled`、`source_invalidated`、`cancelled` |
| `excluded` | 按 §2 原因代码列出数量与中文说明 |
| `warnings` | 代码、数量、可读说明 |
| `scope_fingerprint` | 范围与对象修订摘要（模式、格式、排序后的 ID／`plan_id+version`、选项、`Task.updated_at`／计划版本、时区） |
| `can_download` | 为 false 时禁用下载并说明原因 |

必须展示的警告代码：`date_only_ddl`（仅有日期，按全天导出）、`missing_due_date`（清单保留、ICS 排除）、`source_invalidated`（来源提示已省略）、`cancelled_task`（已排除）、`unscheduled_task`（清单标未排入、ICS 排除）、`buffer_block`（已排除）、`fixed_event_disabled`／`fixed_event_out_of_range`、`empty_export`（结果为空）、`timezone_unreadable`（拒绝导出，不回落 UTC）、`no_accepted_plan`（拒绝导出）、`stale_export_scope`（重新预览）。

下载请求重复完整范围并携带 `scope_fingerprint`；服务端重新计算，范围、计划版本或对象修订与预览不一致时返回 409，要求重新预览。空范围、空结果、无已接受计划、时区不可读时返回 4xx 并给出中文原因，不生成空文件。文件名只用日期：`campusflow-tasks-YYYYMMDD.csv`、`campusflow-plan-YYYYMMDD.ics`，不含课程名、学期名或人名。响应 `Content-Type` 为 `text/csv; charset=utf-8` 或 `text/calendar; charset=utf-8`，导出文件不在服务端留存。

## 5. 隐私规则

- 只导出显式选择的任务或当前已接受计划；没有默认“全部任务”，服务端始终按当前 `workspace_id` 过滤。
- 默认不包含：任务描述、截止备注、计划块理由与约束来源、资料原文／片段／引用引文、发布方、来源链接、候选字段、教师与教学班、截图中的姓名／头像／联系方式。
- 来源提示最多为 `Material.title`、`MaterialVersion.version_no` 和 `SourceCitationOut.locator_type/locator_value`；来源已删除、归档或不可定位时整体省略。用户可在预览关闭 `include_source_hints`。
- 固定日程只在用户显式勾选时导出；其 `location` 可能包含房间信息，预览逐条列出后再下载。
- 标题按存储内容导出，可能在用户自己填写时含姓名；预览逐条展示标题，用户可取消选择。系统不自动改写用户标题。
- 文件名、日志和错误信息只含数量、ID 与原因代码，不写标题、课程名或原文。导出不构成外部写入授权，不需要日历账号授权。

## 6. 互操作约定（A019／A021）

- 时区来源：任务模式的带时刻截止按 `WorkspaceData.timezone`（即 `SettingsOut.timezone`）解释；计划模式按 `PlanDraftOut.timezone`（接受时的输入快照）解释。缺失或非法 IANA 时区时报错并阻止下载，不默认按 UTC 处理，也不把 `PlanDraftOut.from_data` 的 `UTC` 兜底当作真实时区。
- 清单中的带时刻字段写 ISO 8601 并带来源时区偏移；ICS 中带时刻值统一换算 UTC，只有日期值保持浮动 `DATE`，因此不需要 `VTIMEZONE`，跨时区显示为同一时间点。
- DST：已存时间点本身带时区，换算 UTC 无歧义；只有无法唯一确定的墙上时间才拒绝导出，不猜测 `fold`。
- 每周固定日程使用现有 `GET /api/v1/fixed-events/occurrences`（T011）展开，只在计划范围内取发生项，不自行重写日期，也不输出 `RRULE`；发生项起止不裁剪。
- UID 稳定性：同一任务、同一已接受计划的同一块重复导出时 UID 不变，客户端更新而非新建；任务截止事件用 `Task.updated_at` 写 `LAST-MODIFIED`，计划块用 `PlanDraftOut.version` 写 `SEQUENCE`。
- 已知限制：接受新草案会生成新的 `PlanBlock.id`，UID 随之变化；再次导入可能与旧计划事件重复。A020 在下载说明中提示先删除旧计划事件。固定日程发生项的 UID 以开始时间区分，时间修改会生成新 UID。
- A021 至少验证：仅日期 DDL 在正负时区都保持同一天全天；带时刻 DDL 在另一时区显示同一时间点；计划块跨 DST 保持时间点；同一计划重复导出 UID 一致；特殊字符、长标题和中文在 CSV／ICS 中往返正确；非法时区被拒绝。

## 7. 未覆盖

- 不导出 Word／PDF 附件、图片或网页原文；不提供字段级自定义列。
- 不导出提醒、冲突、问答历史、候选核对记录；这些对象不属于“所选任务／已接受计划”。
- 不写入外部日历、不发送邮件或群消息；外部日历写入属于 M9，另按确认与结果跟踪规则处理。
- 不保留服务端导出历史，不提供“导出全部数据”入口。
