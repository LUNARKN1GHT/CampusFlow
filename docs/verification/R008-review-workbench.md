# R008 集中核对列表与详情界面

任务：[R008 #125](https://github.com/LUNARKN1GHT/CampusFlow/issues/125)，父任务 #12（E31）。作者：李亦凡（LUNARKN1GHT）。日期：2026-10-15。

## 目标与用户决定

交付物是「展示原文、缺失项、适用范围和确认忽略操作」。R001 已经提供候选清单与计数，但清单里的每条候选此前只能看到标题与原因代码：既没有逐字段取值、出处与原文引用，也没有可执行的核对转换入口，因此界面上无法完成核对。本项补齐支撑界面所需的一个只读详情接口与一个核对状态转换接口，并把「待核对」页面接成可用的核对工作台。

关键取舍：

- 详情接口只组合既有读模型（X001 字段与证据、R002 修正、R003 转换留痕、R007 引用可验证规则），不新增表、不新增列，也不需要迁移。
- 状态转换复用 R003 的应用层守卫，界面不自己判断「能不能确认」；可执行转换由后端在详情里以 `allowed_transitions` 给出，前端只渲染返回的按钮。
- 详情一次返回字段、证据、修正留痕与核对留痕，避免核对界面多次往返；列表仍是 R001 的最小字段。
- 缺少必须字段时不静默确认：界面必须先填核对说明并显式勾选，请求才被接受（后端同样强制）。

## 接口

| 接口 | 说明 |
| --- | --- |
| `GET /api/v1/candidates/{candidate_id}` | 候选核对详情：适用范围、来源资料与版本、逐字段取值与出处、原文引用与可验证状态、缺失必须字段、允许的核对转换、修正与核对留痕 |
| `PATCH /api/v1/candidates/{candidate_id}/review-status` | 核对状态转换，成功后返回与 `GET` 同结构的最新详情 |

两者随 `candidates_api.router` 挂载在 `/api/v1` 下，要求本机会话（`require_session`），只读写当前工作空间的数据；跨空间编号与不存在同样返回 404（W006 口径）。

### `PATCH` 请求体

| 字段 | 含义 |
| --- | --- |
| `status` | 目标核对状态，只接受 `pending`／`confirmed`／`ignored`／`superseded`；传任务执行进度值一律 422 |
| `reason` | 核对说明，最长 500 字；缺必须字段而显式确认时必填 |
| `acknowledge_missing` | 是否已显式确认「缺少必须字段仍要继续」，默认 `false` |

操作者取当前会话用户名，不由请求体提供。领域错误（非法转换、缺必须字段未确认）返回 400 与中文原因，界面直接展示。

### 详情返回字段

| 字段 | 含义 |
| --- | --- |
| id / kind / review_status / created_at | 候选编号、事项类型、核对状态、提取时间 |
| scope | `course_id`／`semester_id`／`class_name`，另带 `course_name`／`semester_name`；名称只取本空间的课程与学期，取不到为 null |
| source | `material_id`／`material_title`／`version_id`／`version_no`，用于回到资料库定位原文 |
| fields[] | 每个字段并列给出原始提取（`value`／`origin`）与叠加 R002 修正后的生效值（`effective_value`／`effective_origin`），另有 `explanation`、`missing_reason`、`verifiable` 与 `evidence[]` |
| fields[].evidence[] | `chunk_id`／`quote`／`locator_type`／`locator_value`／`verifiable`；片段已不存在时定位信息为空串，不伪造位置 |
| revisions[] / review_changes[] | R002 字段修正留痕与 R003 核对状态留痕，均按时间顺序 |
| review_reasons[] | 缺失或需核对原因（`code`／`message`／`field`） |
| missing_required_fields | 按 R003 的 `REQUIRED_FIELDS` 计算，仍为空的必须字段 |
| allowed_transitions | 后端给出的可执行转换，顺序固定为确认→忽略→已失效→退回待核对 |

`fields[].verifiable` 沿用 R007 规则：`document`／`inferred` 出处且声明了取值的字段，若没有任何一条引用仍能定位到原文，则该字段整体标为不可验证；`user`／`suggestion` 出处本来就不要求原文引用，不算失效。

## 实现要点

- **原始提取与生效值分开给**：详情按 `candidate.draft.fields` 逐条输出原始值与出处，再叠加 `effective_draft()` 的生效值填 `effective_*`、`explanation`、`missing_reason`。引用与可验证状态只按原始字段的证据判定，用户修正不会改写或新增原文引用（R002 语义）。
- **不伪造引用**：证据定位信息来自候选绑定版本的 `SourceChunk`。片段文本被就地改写后 `quote` 不再逐字出现，`citation_is_verifiable` 返回 false，界面显示「引用已失效，无法在原文中找到」但保留定位信息；片段被删除时 `candidate_field_evidence` 随外键级联删除，详情只剩字段值与「该字段没有可定位的原文引用」。
- **确认守卫留在应用层**：`PATCH` 调 `set_candidate_review_status`，缺必须字段且 `acknowledge_missing` 为 false 时抛 `DomainError`，路由映射为 400；界面因此可以安全地把后端返回的中文原因原样显示，不需要自己复制 `REQUIRED_FIELDS` 规则。
- **转换按钮由数据驱动**：界面只渲染 `allowed_transitions` 返回的按钮（确认／忽略／标记失效／退回待核对），已忽略与已失效项不会显示「确认」；任务执行进度值在接口层就被 422 拒绝，两套状态在界面上也不混用。
- **核对错误不会被成功提示掩盖**：`transition()` 失败分支只写 `actionError` 并把 `successMessage` 清空，成功提示与错误提示分别渲染在按钮上方的 `p.form-error[role=alert]` 与 `p.success-message[role=status]`；失败后不重新加载详情，用户已填写的核对说明与勾选状态保留，可直接修正重试。
- **原文定位固定到引用所属版本**：`MaterialSource.vue` 新增可选 `initialVersionNo`／`initialChunkId`，`LibraryView.vue` 读取 `/library?material=&version=&chunk=` 查询参数打开对应资料。候选的引用可能属于旧版本，若一律按最新版本打开，界面会把高亮不到摘录的片段显示成「可验证引用」，因此详情里的每条「定位原文」与「到资料库查看原文定位」都带上候选的 `version_no`。
- **复选框在表单里的样式修正**：`.stack-form label` 的 `display: grid` 与全宽输入会盖过 `.switch-field`，使「我已确认缺少必须字段」的复选框被拉成独立一行（资料库上传表单的「允许重复导入相同文件」同样受影响）。`styles.css` 增加 `.stack-form .switch-field` 与 `.stack-form .switch-field input` 两条覆盖，恢复同行显示。

## 界面

- `frontend/src/views/ReviewView.vue`：待核对列表页。六个筛选（核对状态／事项类型／学期／课程／来源资料／缺失原因），状态默认「待核对」，选「全部状态」时显式带上四种状态（后端省略 `status` 只返回待核对）；计数徽标显示当前筛选结果条数，下方 `status-breakdown` 显示 R001 汇总的状态分布；每行给出标题（未提取到显示「未提取到标题」）、类型与核对状态徽标、适用范围、来源资料与版本、缺失原因芯片和「核对」按钮。
- `frontend/src/views/ReviewDetail.vue`：详情面板。依次为来源与适用范围、待补充与待核对项、字段核对（生效值／原始提取并列、四种出处徽标、逐条引用与定位链接、失效标记）、修正留痕、核对留痕、核对操作（说明输入框、缺必须字段时出现的显式确认勾选、后端给出的转换按钮）。
- `frontend/src/api/review.ts`：`candidatesApi.list／summary／get／setReviewStatus`；`status` 是可重复参数，列表接口内用 `URLSearchParams.append` 显式拼接。
- 导航「待核对」从占位项改为可用入口，路由 `/review`；`/api/v1/candidates/{id}` 与既有的 `/candidates/summary` 不冲突（后者先注册，测试覆盖）。

## 验收对照

| Issue #125 验收条件 | 实现与证据 |
| --- | --- |
| 用户可以从待核对入口处理全部候选 | 导航「待核对」进入 `/review`，默认列出待核对候选，「全部状态」显式带四种状态，因此已确认／已忽略／已失效同样能在同一入口打开并继续处理；每行都有「核对」按钮，详情按后端返回的 `allowed_transitions` 给出确认／忽略／标记失效／退回待核对。浏览器实测：4 条候选（任务／日程／规则各类型）在默认筛选下全部列出，切「全部状态」后计数分布显示「待核对 3 · 已确认 1」，按类型筛选「日程」得 1 条、按缺失原因筛选「缺标题」得 1 条。接口层见 `test_ignore_transition_updates_status_and_allowed_transitions`（已忽略只能退回待核对）、`test_review_status_patch_and_task_progress_are_not_mixed` |
| 核对错误不会被成功提示掩盖 | 缺必须字段的候选点「确认」：后端返回 400 与中文原因，界面在按钮上方显示 `p.form-error[role=alert]`，成功提示为空且状态仍为待核对；填写说明并勾选显式确认后重试成功，才出现 `p.success-message[role=status]` 并刷新留痕。浏览器实测：错误文案「缺少必须字段：title；请先补充字段，或显式确认后继续」原样显示、无成功提示、核对状态未变，且失败后已填写的说明保留可直接重试。接口层见 `test_confirm_with_missing_required_field_needs_explicit_acknowledgement` |

## 验证

`tests/test_candidate_review_detail.py`：**8 passed**。使用合成通知文本、真实 PostgreSQL 测试库与既有资料导入／候选保存／字段修正入口，不调用真实模型。覆盖：

- 详情返回适用范围（含本空间课程与学期名称）、来源资料与版本、逐字段原始值与生效值、四种出处、原文引用与定位信息；缺值字段只给缺失原因不伪造取值；`suggestion` 字段无引用也不算失效；`/candidates/{id}` 不遮住 `/candidates/summary`。
- R002 修正后原始提取与生效值并列展示，引用仍指向原文，修正留痕出现在 `revisions`。
- 片段文本被就地改写后引用标为不可验证但保留定位信息；片段删除后引用清空、字段值仍如实显示、不受影响的字段不误标。
- 缺必须字段的确认先 400 并给出中文原因，显式确认后转换成功并写核对留痕；忽略转换与「已忽略不能直接确认」；已确认只能退回待核对或标记失效。
- 状态转换只接受 `ReviewStatus`，任务执行进度值一律 422。
- 未知编号与跨空间候选（详情与转换、应用层直调）一律 404。

验证命令与结果：

```bash
cd backend && uv run pytest tests/test_candidate_review_detail.py -q   # 8 passed
cd backend && uv run pytest -q                                       # 668 passed（完整后端回归）
cd backend && uv run ruff check .                                    # All checks passed
cd backend && uv run ruff format --check .                           # 174 files already formatted
cd frontend && npm run build                                         # vue-tsc 通过，vite built
```

数据库无变化：本项只读 `extracted_items`、`extracted_fields`、`candidate_field_evidence`、`candidate_field_revisions`、`candidate_review_changes`、`material_versions`、`source_chunks`、`courses`、`semesters`，不新增列或表，**无需新的 Alembic 迁移**；`alembic heads` 实测单一 head：`r006_event_source_invalidation (head)`。

浏览器验收（本机真实服务，非 mock）：后端 `uvicorn campusflow.main:app` 指向独立验收库 `campusflow_r008`（`alembic upgrade head` 后用合成通知种子：1 份文本资料、4 个段落片段、任务／日程／规则共 4 条候选，其中 1 条缺标题），前端 `npm run dev`，以 `student` 登录。核对内容：导航出现「待核对」→ 列表 4 条候选与原因芯片 → 筛选（状态／类型／缺失原因）→ 详情面板的来源、适用范围（课程与学期名称）、待补充项、字段生效值与原文引用、`定位原文` 跳转资料库并选中对应版本与片段（`aria-pressed=true`、原文高亮）→ 缺标题候选的确认失败与显式确认成功 → 留痕与状态分布刷新。

自动化 e2e：`frontend/tests/e2e/review.spec.ts` 只断言登录后能进入 `/review`、标题与筛选控件存在、列表在空数据时给出引导文案，不依赖种子候选。

## 未接入项

- 逐字段修正的界面入口由 R009 接入：后端 `revise_candidate_field` 与详情的 `revisions` 已就绪，本项界面只展示修正留痕，不提供编辑表单。
- 确认后生成正式任务／日程的入口由 R009 接入（R004／R005／R006 已提供转换用例）。
- 批量确认、忽略与合并重复候选不在本项范围；状态转换仍是 R003 的单条入口。
- `candidate_field_evidence` 的外键是 `ON DELETE CASCADE`，片段删除时证据行一并删除，因此「片段消失但证据仍在、只剩空定位」的分支目前只能由数据修复产生，接口保留该防御处理并有单测覆盖取值语义。

## 分支与前置

从父分支 `issue/#12` 创建 `issue/#125`（当前基于 `issue/#118`，即 R001 的列表接口之上），本项 PR 的合并目标为 `issue/#12`。前置 R001 #118（列表与计数）、R002 #119（字段修正留痕）、R003 #120（状态转换与确认守卫）、R007 #124（来源详情与引用可验证规则）、D020 #104、U002 #69 已合并或在链上；R004 #121／R005 #122／R006 #123 提供确认后生成正式事项的转换用例，界面入口由 R009 完成。
