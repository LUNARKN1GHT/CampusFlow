# R001 待核对事项列表与原因筛选

任务：[R001 #118](https://github.com/LUNARKN1GHT/CampusFlow/issues/118)，父任务 #12（E31）。作者：李亦凡（LUNARKN1GHT）。日期：2026-10-15。

## 目标与用户决定

交付物是「按课程、来源和缺失原因列出待核对候选及计数」。本项把 X001/R002/R003 已落库的候选、核对状态与缺失原因读出来，形成受登录会话与工作空间授权保护的两个只读 HTTP 接口；清单本身只做列表与计数，逐字段取值、证据与定位仍走 R007 的来源详情接口。

关键取舍：

- 列表接口只接受核对状态（`ReviewStatus`），不接受任务执行进度，两套状态在接口层就不混用。
- 列表只返回判断所需的最小字段（标题、类型、核对状态、适用范围、缺失原因、来源资料与版本），不在列表里铺开字段和引用。
- 缺失原因按原因代码筛选与计数，代码在应用层过滤，不在 SQL 里硬编码已知代码。

## 接口

| 接口 | 说明 |
| --- | --- |
| `GET /api/v1/candidates` | 待核对候选列表，按条件筛选 |
| `GET /api/v1/candidates/summary` | 同一筛选范围内的候选计数（按核对状态与缺失原因分别统计） |

两者都随 `candidates_api.router` 挂载在 `/api/v1` 下并要求本机会话（`require_session`），只返回当前空间的数据。

### 查询参数

| 参数 | 接口 | 含义 |
| --- | --- | --- |
| `status` | 列表 | 核对状态，可重复传（多选）；取值只有 `pending`／`confirmed`／`ignored`／`superseded`；**不传时默认只返回 `pending`**，其他状态需显式指定 |
| `kind` | 列表、计数 | 事项类型 `task`／`event`／`rule`，可多选 |
| `course_id` | 列表、计数 | 按适用课程筛选 |
| `semester_id` | 列表、计数 | 按适用学期筛选 |
| `material_id` | 列表、计数 | 按来源资料筛选（候选绑定版本所属资料） |
| `reason` | 列表 | 缺失原因代码，命中该原因的候选才返回 |

`course_id`、`semester_id`、`material_id` 必须属于当前空间，否则与不存在同样返回 404（W006 口径），避免通过筛选探测其他空间的编号。

### 返回字段

列表项（`CandidateSummaryOut`）：

| 字段 | 含义 |
| --- | --- |
| id | 候选编号，供详情、修正与状态转换接口使用 |
| kind | 事项类型 task／event／rule |
| review_status | 核对状态（待核对／已确认／已忽略／已失效） |
| title | 原始提取的标题；未提取到为 null，不拿别的字段凑数 |
| course_id / semester_id / class_name | 适用范围；未关联时为 null |
| source_material_id / source_version_id / version_no | 来源资料与版本号 |
| review_reasons | 缺失或需核对原因列表，每项含 `code`、`message` 与可选 `field` |
| created_at | 候选创建时间 |

计数（`ReviewCountsOut`）：`total` 为筛选范围内的候选总数；`by_status` 按核对状态分别计数（覆盖全部状态，因此可直接显示「待核对 3／已确认 1」）；`by_reason` 按原因代码计数，同一候选上重复出现的同一代码只计一次。计数的覆盖范围与列表一致，但不含 `reason` 筛选，否则各原因数量之和会等于筛选后的总数而失去分布含义。

## 实现要点

- **两套状态互不混用**：应用入口 `application.candidates.list_review_candidates` / `review_counts` 的 `statuses` 参数类型固定为 `ReviewStatus`，路由的 `status` 查询参数同样是 `list[ReviewStatus]`。传入任务进度值（`not_started`、`in_progress`、`blocked`、`completed`、`cancelled`）会被请求校验拒绝（422），不会退化成「无条件返回全部」；列表响应里也没有进度字段。默认值集中在 `DEFAULT_REVIEW_STATUSES = (ReviewStatus.PENDING,)`。
- **列表只给最小字段**：仓储 `SqlCandidateRepository.list_for_review` 返回 `CandidateSummaryData`，标题从 `extracted_fields` 里单独取 `name == "title"` 的一条批量查出（非字符串值视为空），不加载字段全表、证据与修正记录；逐字段取值、出处与可定位引用仍由 R007 的 `GET /api/v1/tasks/{id}/source` 等详情接口读取。
- **原因在应用层按代码过滤**：缺失原因存在 `extracted_items.review_reasons` 的 JSONB 列里，代码取值随 X 系列提取任务继续增加（现有如 `missing_year`、`missing_time`、`invalid_date`、`relative_date`、`deadline_needs_review`，以及各提取入口按缺失字段生成的 `missing_<field>`）。因此在 SQL 里硬编码已知代码会漏掉后续新增原因，端口约定由调用方在应用层过滤：列表按 `reason` 精确匹配代码，计数按候选去重后的代码集合累加。新增代码无需改本接口即可出现在筛选与计数中。
- **未覆盖范围可从列表进入**：每条列表项都带 `source_material_id` 与 `version_no`（来源版本经 `MaterialVersion` 关联查出），界面可据此跳到该资料该版本的原文片段列表（如 `GET /api/v1/materials/{id}/versions/{version_no}/chunks`）查看解析未覆盖范围与原文定位；`review_reasons` 同时给出该条候选需要核对的具体缺失点。

## 验收对照

| Issue #118 验收条件 | 实现与证据 |
| --- | --- |
| 待核对与任务进度筛选互不混用 | 接口只接受 `ReviewStatus`，默认 `pending`；任务进度值返回 422、响应不含进度字段。见 `test_review_status_and_task_progress_filters_are_not_mixed`；默认只列待核对与其他状态需显式指定见 `test_list_defaults_to_pending_items_with_reasons_and_source` |
| 未覆盖范围也可从列表进入查看 | 列表项带 `source_material_id`、`source_version_id`、`version_no` 与 `review_reasons`，可据此跳资料版本查看原文片段与未覆盖范围。见 `test_list_entries_link_to_source_version_for_uncovered_ranges`；按资料/课程/学期筛选见 `test_list_filters_by_course_kind_and_material`，跨空间筛选 404 见 `test_foreign_workspace_course_or_semester_filter_returns_not_found` |
| 交付物：按课程、来源和缺失原因列出待核对候选及计数 | `kind`／`course_id`／`semester_id`／`material_id`／`reason` 筛选与 `summary` 计数（`total`／`by_status`／`by_reason`，同一候选重复原因只计一次）。见 `test_reason_filter_and_counts_group_by_code` |

## 验证

`tests/test_candidate_review_list.py`：**6 passed**。使用合成通知、真实 PostgreSQL 测试库与既有候选保存／确认入口，不调用真实模型。覆盖：

- 默认只列待核对项，携带原因与来源资料／版本；`status` 单选与多选取值。
- 原因筛选命中与未知代码返回空；计数总量、按状态分布与按原因代码分布，重复原因只计一次。
- 按课程、学期、类型、资料筛选；不存在的 `material_id` 返回 404。
- 核对状态与任务进度不混用；跨空间的课程／学期筛选返回 404，且不影响无条件列表。
- 列表项的来源资料与版本号可用同一版本片段接口定位原文。

验证命令与结果：

```bash
cd backend && uv run pytest tests/test_candidate_review_list.py -q   # 6 passed
cd backend && uv run pytest -q                                       # 660 passed（完整后端回归）
cd backend && uv run ruff check .                                    # All checks passed
cd backend && uv run ruff format --check .                           # 173 files already formatted
```

数据库无变化：本项只读已有 `extracted_items`（含 `review_reasons` JSONB 列）与 `extracted_fields`、`material_versions`，不新增列或表，**无需新的 Alembic 迁移**；`alembic heads` 实测单一 head：`r006_event_source_invalidation (head)`。

前端本次无改动，待 R008 接入。

## 未接入项

- 核对列表界面与逐条详情交互由 R008 完成；本项只提供只读接口，页面尚无入口。
- X008 #115（解析未覆盖与来源冲突形成明确核对原因）还未产出对应原因代码：列表按现有 `review_reasons` 数据如实展示，`truncated`、来源冲突一类代码在 X008 落地后会自动出现在筛选与计数中，本接口不需要改动。
- 未在列表响应中返回字段与引用，避免与 R007 详情接口的证据语义重复；也不做批量确认／忽略，状态转换仍是 R003 的单条入口。

## 分支与前置

从父分支 `issue/#12`（已含 R002–R005、R007）创建 `issue/#118`，本项 PR 的合并目标为 `issue/#12`。前置 X001 #108（候选与原因分表存储）、R003 #120（核对状态与留痕）已合并；R007 #124 提供详情读取，两者共同支撑核对列表交互（R008）。
