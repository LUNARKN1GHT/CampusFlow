# M2 资料导入与来源定位：集成验收

关联任务：#343。父组：E20 #8、E21 #9、E22 #10。验证日期：2026-10-13。

本记录覆盖 M2 的 PDF、图片、粘贴文本私有导入、来源定位、可恢复作业与删除生命周期。
本分支已通过本机及 Linux + PostgreSQL 17 集成验证；本记录用于父组 PR 与里程碑确认，最终合并与关闭状态以 GitHub 为准。
不以本记录自动关闭父 Issue，不把 M6 的 Word/网页成果带入 M2。

## 整合基线与发现的缺陷

#340/#341/#342 已分别补齐 E20/E21/E22 的历史串联合并遗漏。验收分支聚合三组父分支，
并修复真实端到端验证发现的缺口：

- 作业详情依赖浏览器 localStorage，换浏览器后丢失范围和重试入口。新增受空间授权保护的
  `GET /api/v1/jobs?material_id=…`，按版本与创建时间降序恢复解析作业；查询失败明确提示。
- 同步文本解析完成仍停在 pending。现在事务内保存 done，并验证持久化后的状态。
- 部分成功只显示 `region`，不显示坐标或原因。现在显示页码／段落／归一化区域及经清洗的
  失败原因；过长范围明确标明总数与截断，不让数据库长度限制使整个作业失败。
- 资料筛选选择项没有请求新列表。现在学期、课程、来源和状态变化都触发实际后端筛选。
- 发布日期被补成午夜。页面只接受完整时间与时区；API 拒绝只有日期的时间戳输入，原始日期
  可保留在正文，未知发布时间保持空值。
- 文本原文面板只展示解析片段，没有核实原文件可读。现在读取实际私有原文，高亮所选段落；
  读取失败显示无法验证，不将临时网络失败断言为永久删除。
- 删除后保留任务已有来源失效字段，但任务页未显示。现在显示来源已删除与引用不可用说明。
- 生命周期面板按资料编号重建，避免切换资料时保留上份预览；图片按实际显示尺寸同比例高亮。

## 门槛与证据

| M2 门槛 | 可复现证据 | 本机结论 |
| --- | --- | --- |
| 文本、PDF、图片可私有导入并定位 | `test_text_import.py`、`test_source_read.py`、`test_pdf_parser.py`；M2 浏览器流程读取第二段、PDF 第二页与图片区域 | 通过 |
| 部分解析失败可见且不当作没有要求 | `test_job_fault_injection.py`、`test_jobs_api.py`；真实 Worker 产出 partial，页面展示区域和原因 | 通过 |
| 可重试且重复消息幂等 | `test_delivery_idempotency.py`、`test_job_retry_timeout.py`；浏览器在新上下文恢复作业，partial → failed → done，查询服务端状态 | 通过 |
| 相同文件不重复建档 | `test_duplicate_upload.py`；浏览器二次导入同一 PDF，列表仍仅一份 | 通过 |
| 来源与空间隔离 | `test_source_read.py`、`test_jobs_api.py`：其他空间与不存在都返回 404；删除后无法查询作业 | 通过 |
| 删除前确认展示范围，变化后重新确认 | `test_delete_impact.py`；浏览器预览后创建合成关联任务，旧指纹由后端拒绝，再次预览后成功 | 通过 |
| 删除后原文与派生内容失效，个人任务不静默消失 | `test_material_delete_invalidation.py`、`test_job_write_guard.py`；浏览器核对保留任务及任务页引用不可用提示，原文件接口 404 | 通过 |
| 归档与删除独立，恢复保留原文 | `test_archive.py`；浏览器归档隐藏、包含归档可见、恢复后显示 | 通过 |
| 原始日期不补造时刻 | `test_text_import.py::test_date_only_publication_is_not_fabricated_as_midnight` | 通过 |
| M1 手动管理无回归 | 既有三项 `manual-management.spec.ts` 和全量后端 | 通过 |

## 本机实际执行

环境：macOS ARM64、Python 3.12、PostgreSQL 16.14、临时 Redis 7.4.2、Chromium。
后端与浏览器分别使用新建的 `campusflow_m2_api_test` 和 `campusflow_m2_e2e_test`；
Redis 仅监听本机 16379，使用库号 15；原文保存在独立临时目录，没有清空日常数据。

- `uv run --locked pytest -q`：255 passed。
- `uv run --locked ruff check .` 与 `ruff format --check .`：通过。
- `npm run build`（含 vue-tsc）：通过。
- `CAMPUSFLOW_M2_E2E=1 npm run test:e2e`：4 passed，包含三项 M1 和一项 M2 多步骤流程。
- `git diff --check`：通过。
- 图片来源面板截图已检查：原图、区域高亮、引用标识、适用范围可见。

首次回归发现同步文本测试仍期待 pending、部分失败仅显示 region；修复后重新执行通过。

## 运行真实队列浏览器回归

使用新的空 `_test` 数据库，先执行迁移。API、测试命令和 Worker 必须共享下面的环境变量：

```bash
export CAMPUSFLOW_DATABASE_URL=postgresql+psycopg://campusflow:campusflow@127.0.0.1:5432/campusflow_m2_e2e_test
export CAMPUSFLOW_REDIS_URL=redis://127.0.0.1:6379/15
export CAMPUSFLOW_STORAGE_DIR=/tmp/campusflow-m2-storage
# backend/：先迁移，再启动真实 API
uv run --locked alembic upgrade head
uv run --locked uvicorn campusflow.main:app --host 127.0.0.1 --port 8000
# 另一个终端 frontend/，使用同一组环境变量
CAMPUSFLOW_M2_E2E=1 npm run test:e2e
```

测试辅助程序 `scripts/m2_browser_fixtures.py` 生成合成 PDF/图片，在独立进程中启动真正的
RQ `SafeWorker` 并消费真实 Redis 队列；数据库写入、API、文件读取和页面均是真实实现。
视觉识别使用确定性替身，明确覆盖 done/partial/failed，不调用外部模型。辅助程序只接受
显式 `_test` 数据库及非零 Redis 库号；新任务关联仅对 `M2 text …` 合成资料开放。

CI 浏览器任务已加入 Redis 7 并启用同一流程；[PR #344 的 Linux 复验](https://github.com/LUNARKN1GHT/CampusFlow/actions/runs/38367874381)
实际执行 4 项浏览器流程全部通过，日志已确认未跳过 M2。PostgreSQL/Ruff 与前端构建亦通过。
检查名称保留 `Manual management browser flow`，避免破坏已有分支保护引用。

## 边界与后续合并

- 本次真实队列本机运行在 POSIX macOS；Linux RQ Worker 已在上述 PostgreSQL 17 CI 中复验通过。
- 视觉替身只证明处理链路与失败呈现，不证明真实 OCR 准确率。已有真实模型的授权合成样本
  评测见 [D013](../evaluation/d013-vision-adapter.md)，其模型局限继续保留，不扩大到任意学校资料。
- PDF 文字页定位到页；视觉解析提供有效区域时保留坐标，不伪造文字页内坐标。
- 备份生命周期按 [数据保留说明](../data-retention.md) 处理；未配置生产备份服务。
- 核对、RAG 问答、排期、Word/网页导入及多人部署均不属于本次 M2 交付。
- #343 已通过 [#344](https://github.com/LUNARKN1GHT/CampusFlow/pull/344) 合入 #9。父分支集成 PR 为
  [#345](https://github.com/LUNARKN1GHT/CampusFlow/pull/345)、[#346](https://github.com/LUNARKN1GHT/CampusFlow/pull/346)、
  [#347](https://github.com/LUNARKN1GHT/CampusFlow/pull/347)，逐项核对最新 CI 与最终成果可达性。
- 组长在本次对话明确授权本批非作者评审例外，在检查通过后合并并完成 M2；该授权只覆盖
  #344—#347，不伪造独立批准，也不绕过平台保护。完整整合与门槛核对后关闭父组和 M2。
  发布 v0.2.0 另走 main PR。
