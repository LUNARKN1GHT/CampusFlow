# BUG-555 原文读取成功但问答索引失败时的资料状态

任务：[#555](https://github.com/LUNARKN1GHT/CampusFlow/issues/555)。日期：2026-11-11。

## 问题与根因

- `materials.status` 只由解析作业写入：PDF／Word 原文读取成功后即为 `done`；解析作业失败或部分成功才写 `failed`／`partial`。
- 问答索引是独立作业（`processing_jobs.stage = 'embed'`）。索引失败按 Q004 的设计不回写 `materials.status`，避免索引失败掩盖已成功的原文；但资料列表此前只展示 `status`，于是显示“完成”，用户会理解为已经可以问答。
- 前端只按资料读取解析作业（`stage = 'parse'`），索引作业的失败原因没有进入列表或详情。

结论：不能通过把索引失败写回 `materials.status` 来修复（那会丢失“原文已读取”的事实），需要第二条独立状态轴；两条轴都在读取时派生，不新增数据库列或迁移。

## 状态模型

`GET /api/v1/materials`、`GET /api/v1/materials/{id}` 及所有返回资料的入口新增 `processing` 对象：

```json
{
  "status": "done",
  "processing": {
    "text_state": "ready",
    "index": {
      "state": "failed",
      "reason_code": "index_failed_all_blocks",
      "message": "问答索引全部失败，这份资料目前不能用于问答；原文已读取并保留，可查看原文，失败详情见处理记录。",
      "detail": "预期 1 块；完成 0；失败 1；跳过 0",
      "error": "索引全部块失败",
      "job_id": 12,
      "retryable": false
    }
  }
}
```

### 原文读取（`processing.text_state`）

以**当前资料版本的最新解析作业**为准，能区分排队与读取中；没有解析作业的粘贴文本（同步切分）回退到资料 `status`。

| 值 | 含义 |
| --- | --- |
| `queued` | 待读取（作业未投递或排队中） |
| `reading` | 读取中 |
| `ready` | 已读取 |
| `partial` | 部分读取，未覆盖范围继续由作业 `scope` 展示 |
| `failed` | 读取失败 |

### 问答索引（`processing.index`）

以**当前资料版本的最新索引作业**为准。没有作业是“未开始”，不是“可用”；作业完成还必须带索引清单才能确认可用。

| `state` | 含义 |
| --- | --- |
| `not_started` | 未开始（含粘贴文本目前不自动建立索引） |
| `preparing` | 准备中：等待投递、已排队或处理中，用 `reason_code` 区分 |
| `ready` | 可用 |
| `partial` | 部分可用：只有已成功索引的块参与检索 |
| `failed` | 失败 |
| `unknown` | 记录不完整，不能确认可用 |

失败与准备中的稳定原因代码：

| `reason_code` | 触发条件 |
| --- | --- |
| `index_not_started` | 当前版本没有索引作业 |
| `index_waiting_delivery` | 索引作业待处理且尚未成功投递 |
| `index_queued` | 索引作业已排队、尚未领取 |
| `index_running` | 索引作业处理中 |
| `index_ready` | 索引作业完成且带当前清单 |
| `index_timeout` | 作业失败原因为处理超时 |
| `index_failed_all_blocks` | 全部索引块失败 |
| `index_partial_blocks_failed` | 部分索引块失败或解析覆盖不完整 |
| `index_partial` | 部分成功但没有可映射的固定失败文案 |
| `index_source_changed` | 索引来源在处理中变化，需要重建清单 |
| `index_source_unavailable` | 索引来源不可用或已归档 |
| `index_model_profile_mismatch` | 向量配置未登记或与调用模型不一致 |
| `index_failed` | 其它失败 |
| `index_unverified` | 作业已完成但缺少索引清单，不能确认 |
| `index_status_unknown` | 作业状态值无法识别 |

`detail` 是作业已保存的覆盖说明（数量与未覆盖范围，不含正文）；`error` 是已经过 `sanitize_error_reason` 的作业原因；`job_id` 供重试入口使用；`retryable` 复用 `jobs.can_retry`，只有失败／部分成功且最近失败为临时故障才为真。已归档资料的 `message` 额外说明当前不参与问答，恢复后按索引记录生效。

## 实现要点

- `backend/src/campusflow/domain/material_status.py`：纯规则映射，输入是作业事实，不查库、不调用模型，也不读取原文。
- `backend/src/campusflow/domain/indexing.py`：索引失败固定文案集中定义，应用层 `application/indexing.py` 改为引用同一常量，状态派生与写作业不会各写一份字符串。
- `backend/src/campusflow/application/material_status.py`：`build_processing_views` 为整页资料一次性构造两条状态轴。
- `JobRepository.latest_stage_jobs_by_materials`：一条 SQL 取回所有资料的解析／索引作业，按 `version_no DESC, job.id DESC` 选中当前版本的最新作业；没有逐资料查询，也不会让旧版本的失败记录覆盖新版本的成功结果。
- `backend/src/campusflow/api/materials.py`：`MaterialOut.processing` 为必填字段；文本导入、网页导入、列表、详情、归档／恢复、元数据修订、删除影响预览与多文件上传统一使用 `material_out`／`materials_out`，不会出现某些入口缺字段。
- 前端 `frontend/src/views/LibraryView.vue`：列表同时展示“原文已读取”等原文状态与“问答索引失败”等索引状态；失败／部分／无法核对的状态说明直接显示在列表行；每行新增“处理详情”展开区，列出两条轴、原文覆盖、索引覆盖、脱敏失败原因、索引作业号、可重试性与“重试索引”入口。轮询条件包含索引准备中，重试后自动刷新。
- 前端 `frontend/src/api/types.ts` 与 `frontend/src/styles.css`：补充类型与状态标签样式，颜色继续使用语义 token。

数据库无变化：未新增表或列，无 Alembic 迁移，归档／版本／删除语义保持原样。

## 验收对照

| Issue #555 验收条件 | 实现与证据 |
| --- | --- |
| 正常 PDF／Word 读取成功、索引失败时列表不暗示“已可问答” | 列表改为原文／索引两枚状态标签，索引失败显示“问答索引失败”并给出说明；`test_parse_success_but_index_failure_is_not_ready` 断言 `status=done` 的同时 `processing.index.state=failed`、不能用于问答且原文保留 |
| 原文失败、原文部分成功、索引失败和全部完成分别有明确显示 | `test_failed_parse_is_visible_and_index_not_started`、`test_partial_parse_keeps_text_state_partial`、`test_parse_success_but_index_failure_is_not_ready`、`test_parse_success_and_index_success_reports_ready` |
| 状态对应当前资料版本与处理尝试，旧失败不覆盖新成功 | `latest_stage_jobs_by_materials` 按版本号选取；`test_new_version_success_is_not_overridden_by_old_failure` 旧版本索引失败后新版本成功，详情显示 `ready` 且作业号为新作业 |
| 重试成功、刷新页面和重启应用后的显示一致 | 状态全部由数据库作业记录派生，无进程内缓存；`test_retryable_index_failure_is_retried_to_ready` 覆盖失败 → 重试（准备中）→ 执行成功（可用） |
| 未索引的文本不被误标为可问答 | `test_index_not_started_is_never_reported_as_ready`、`test_pasted_text_is_ready_for_reading_but_not_for_qa`（`text_state=ready` 且 `index.state=not_started`） |
| 部分覆盖时提示缺失范围 | `index.detail` 返回作业覆盖说明；`test_partial_index_reports_partial_and_coverage` 断言失败块数量与“未覆盖或未索引内容不会被当作没有要求” |

## 验证

```bash
cd backend && uv run pytest tests/test_material_status_views.py -q   # 13 passed
cd backend && uv run pytest <资料／作业／索引相关 14 个套件> -q     # 90 passed
cd backend && uv run ruff check .                                    # All checks passed
cd backend && uv run ruff format --check .                           # 400 files already formatted
cd frontend && npm run build                                         # vue-tsc 通过，vite built
```

后端全量回归：见提交前最后一次运行结果（`pytest -q`）。

测试使用的是合成资料、确定性假向量与真实 PostgreSQL 测试库；无真实模型调用。测试库以 `CAMPUSFLOW_TEST_DATABASE_URL` 指向本工作树独立库 `campusflow_555_test`，避免与并行任务互相清表。

## 未覆盖项

- 未在 Windows 桌面安装包（报告环境 0.1.0.0）上做真实截图与交互回归；本轮验证为后端接口测试、前端类型检查与构建，未使用真实 PDF／Word 样本重放。
- 事项提取（“事项整理”）没有自动作业记录，当前使用显式入口；本次不伪造第三个状态，只展示原文读取与问答索引两条可核对的轴。提取接入队列后应复用同一派生方式。
- 向量配置缺失造成的永久失败当前不能通过 `/jobs/{id}/retry` 重试；界面会如实显示“当前不可重试”，配置修复与自动重建索引仍由 #131 等任务跟踪。
- 粘贴文本仍不会自动建立索引作业（既有范围）；列表会显示“问答索引未开始”，不会标为可问答。
- 列表状态按资料当前版本的最新作业派生；如果未来出现“新版本已建立但尚未创建解析作业”的写入路径，原文状态会回退到资料级 `status`，需要在该路径落地时补版本级守卫。
