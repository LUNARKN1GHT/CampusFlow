# R009 逐字段修正与确认后生成正式事项的入口

任务：[R009 #126](https://github.com/LUNARKN1GHT/CampusFlow/issues/126)，父任务 #12（E31）。作者：李亦凡（LUNARKN1GHT）。日期：2026-10-15。

## 目标与用户决定

交付物是「在核对界面展示修改来源及生成的任务日程入口」。R008 已经把候选清单、详情与核对状态转换接成可用的工作台，但逐字段修正只能通过应用层用例落库，界面上没有表单；确认后的候选也缺少生成正式事项的入口，因此 M3 的「待核对页面可集中完成修正、确认和忽略」还差最后一环。

关键取舍：

- 逐字段修正复用 R002 的 `revise_candidate_field`，不新增修正语义：仍然只追加记录、原始提取与证据不改写；界面按 R002 的出处规则把可选项限制为「用户补充」与「规划建议」。
- 生成入口复用 R004／R005／R006 的转换用例，界面不自己拼装正式事项，也不复制必需字段规则；失败原因由后端中文消息直出。
- 幂等由既有的「来源候选唯一关联」保证，接口再补一个 `created` 标记，让界面能明确区分「本次新建」与「此前已生成，本次没有重复创建」。
- 详情接口顺带返回已生成的正式事项（`converted`），界面刷新或重新打开候选后仍能给出打开入口，不需要额外请求或本地记忆。

## 接口

| 接口 | 说明 |
| --- | --- |
| `PATCH /api/v1/candidates/{candidate_id}/fields/{field_name}` | 逐字段修正：请求体 `{value, origin, note}`，返回与 `GET` 同结构的最新详情 |
| `POST /api/v1/candidates/{candidate_id}/convert` | 把已确认候选转换为正式事项，返回 `{kind, object_id, title, created}` |

两者都要求本机会话（`require_session`），只读写当前工作空间的数据；跨空间编号与不存在同样返回 404（W006 口径）。修正的操作者取当前会话用户名。

`GET /api/v1/candidates/{candidate_id}` 新增字段：

| 字段 | 含义 |
| --- | --- |
| `converted` | 已生成的正式事项 `{kind, object_id, title}`；尚未生成时为 null，不占位也不猜编号 |

### 修正请求体

| 字段 | 含义 |
| --- | --- |
| `value` | 修正后的取值，可为 null（表示该字段没有取值） |
| `origin` | 修正出处；用户填写的值只能标为 `user`（用户补充）或 `suggestion`（规划建议） |
| `note` | 修正说明，最长 1000 字；清空取值时该项即缺失原因，必须填写 |

标成 `document`／`inferred` 会被应用层拒绝（缺原文证据），返回 400 与中文原因；`origin` 传任务执行进度值一类非法枚举返回 422。

### 转换返回与错误

`POST .../convert` 按候选类型分派：任务 → R004，日程 → R005，规则 → R006。`created` 为 `false` 表示命中既有的来源候选关联、本次没有新建。以下情况返回 400 与中文原因，界面直接展示：

- 候选不是已确认状态（「候选尚未确认…」）；
- 日程候选的开始或结束无法解析出日期与时刻（不按常见时长补造）；
- 规则候选缺标题或规则正文。

## 实现要点

- **按类型分派集中在应用层**：`application/conversion.py` 新增 `CandidateConversion` 与 `convert_candidate`／`find_candidate_conversion`。`find_candidate_conversion` 只按 `source_candidate_id` 查任务、固定日程或课程规则，没有就返回 None；`convert_candidate` 先查已生成事项，再分派到对应转换用例，返回 `(事项, created)`。
- **详情与已生成事项在 API 层组合**：`_detail_out` 把 `review_detail` 的结果与 `find_candidate_conversion` 合并成响应，应用层用例之间不互相依赖（`application/candidates.py` 不反向导入 `conversion`，避免循环导入）。
- **修正规则不在界面重写**：前端只做两项本地校验——数字字段不接受非数字输入、留空取值时必须填说明——其余交由 R002 的校验，错误消息原样展示。
- **错误不掩盖成功**：修正与生成各自有独立的错误提示位（`p.form-error[role=alert]`）与成功提示位（`p.success-message[role=status]`），与 R008 的核对转换一致：失败时保留用户已填内容、清空成功提示，可直接修正重试。
- **打开入口按类型给出**：任务跳 `/tasks?task={id}`、固定日程跳 `/tasks?event={id}`，`TasksView` 与 `FixedSchedule` 读取该参数给对应条目加高亮并滚动到可视区域；课程规则目前只有查询接口（`GET /api/v1/rules`）没有页面，界面只展示编号与标题，并说明规则页面尚未开放，不伪造路由。

## 界面

- `frontend/src/views/ReviewDetail.vue`：字段核对区每个字段增加「修正」按钮，展开内联表单（一次性只开一个）：修正后的取值、修正出处（用户补充／规划建议）、修正说明；保存成功后替换详情、关闭表单并提示，原始提取与修正留痕随之更新。新增「生成正式事项」区：未确认只给说明，已确认且未生成时给按类型命名的生成按钮，已生成时给出编号、标题与打开入口；生成提示区分「已生成 #N」与「此前已生成过同一事项，本次没有重复创建」。
- `frontend/src/views/TasksView.vue`、`frontend/src/components/FixedSchedule.vue`：按 `?task=`／`?event=` 高亮并滚动到对应条目。
- `frontend/src/api/review.ts`：`candidatesApi.reviseField` 与 `candidatesApi.convert`。

## 验收对照

| Issue #126 验收条件 | 实现与证据 |
| --- | --- |
| 确认后可打开正确正式事项 | 已确认候选的生成按钮调用 `POST .../convert` 得到 `object_id`，界面给出 `/tasks?task={id}`（任务）或 `/tasks?event={id}`（固定日程）的打开入口，详情里的 `converted` 让重新打开候选后仍能直达同一事项。接口层见 `test_convert_task_creates_once_and_reports_existing_on_retry`（返回的任务 `course_id`、标题与详情 `converted` 一致）、`test_convert_event_and_rule_by_kind`（固定日程与课程规则各自出现在 `GET /api/v1/fixed-events`／`GET /api/v1/rules` 的首条）。浏览器实测：候选 #1 生成任务 #1 后点「打开任务」进入 `/tasks?task=1`，任务行带 `is-highlighted` 且内容为该候选标题与修正后的 90 分钟；候选 #3 生成课程规则 #1，界面显示编号与标题并说明规则页面尚未开放 |
| 重复点击或请求重试不出现重复事项 | 幂等由 `tasks.source_candidate_id`／`fixed_events.source_candidate_id`／`course_rules.source_candidate_id` 的唯一关联保证；`convert_candidate` 命中既有事项时直接返回并把 `created` 置为 false，界面提示「此前已生成过同一事项，本次没有重复创建」。见 `test_convert_task_creates_once_and_reports_existing_on_retry`（连续转换后 `GET /api/v1/tasks` 仍只有 1 条）、`test_convert_event_and_rule_by_kind`（日程与规则同样幂等）。浏览器实测：对同一候选连续三次 `POST /candidates/1/convert` 都返回 `object_id: 1`、`created: false`，任务列表仍为 1 条 |

交付物「在核对界面展示修改来源」：字段修正表单把出处限制在「用户补充／规划建议」，修正后详情并列显示原始提取（含原出处）与生效值（含新出处），修正留痕给出前后取值、出处变化、操作者与说明。见 `test_field_revision_through_http_keeps_original_and_records_operator`、`test_revision_cannot_claim_document_origin_and_needs_note_when_cleared`、`test_unknown_field_or_progress_value_origin_is_rejected`、`test_corrected_field_value_feeds_conversion`（修正后的取值参与 R004 转换）。

## 验证

`tests/test_candidate_review_actions.py`：**8 passed**。使用合成通知、真实 PostgreSQL 测试库与既有的资料导入／候选保存／核对入口，不调用真实模型。覆盖：

- 修正经 HTTP 落库后原始提取与证据保留、生效值与出处更新、留痕记录操作者与说明；用户填写的值不能声明为原文规定；清空取值必须给出缺失原因，且清空必须字段后确认被守卫拦下；未知字段 400、非法出处 422。
- 修正后的取值参与转换：补全截止时刻后生成的任务带对应 `due_date`／`due_time`。
- 任务、日程、规则三种类型各自生成对应的正式事项并保持幂等；详情 `converted` 与生成结果一致。
- 未确认候选转换 400；日程起止只有相对写法时转换 400 且不落库；规则缺正文时确认需显式确认但转换仍拒绝。
- 未知编号与跨空间候选（接口与应用层直调）一律 404。

验证命令与结果：

```bash
cd backend && uv run pytest tests/test_candidate_review_actions.py -q   # 8 passed
cd backend && uv run pytest -q                                        # 690 passed（完整后端回归）
cd backend && uv run ruff check .                                     # All checks passed
cd backend && uv run ruff format --check .                            # 180 files already formatted
cd frontend && npm run build                                          # vue-tsc 通过，vite built
cd backend && uv run alembic heads                                    # r007_course_rules (head)
```

数据库无变化：本项不新增表或列；单一 head 仍由 R006 的 `r007_course_rules` 提供（R009 分支包含 R006 #123）。

浏览器验收（本机真实服务，非 mock）：后端指向独立验收库 `campusflow_r008`（`alembic upgrade head` 到 `r007_course_rules` 后用合成通知种子：1 份文本资料、4 条候选），前端 `npm run dev`，以 `student` 登录。核对内容：候选 #1 对「截止时刻」补值并说明 → 生效值 18:00、原始提取仍为（空）、留痕记录操作者与说明；对数字字段填非数字被本地拦下、改填 90 后保存成功；确认后生成任务 #1 并「打开任务」跳转高亮；对已生成候选重复请求转换返回 `created: false`；候选 #2（日程起止为相对写法）确认后生成被拒绝并原样显示中文原因；候选 #3 生成课程规则 #1，界面如实说明规则页面尚未开放。

## 未接入项

- 课程规则只有查询接口、没有页面，界面因此不提供跳转；规则页面开放后把「生成课程规则」的入口接上即可。
- 修正只支持逐字段覆盖与留空，不做批量修正、字段级撤销或修正历史回滚（留痕是追加式的，R002 语义）。
- 日程与任务的高亮定位依赖列表页的筛选条件：目标事项若被当前筛选排除在外，界面只跳转到该页而不改变筛选。
