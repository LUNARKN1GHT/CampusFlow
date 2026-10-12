# M1 手动学习事务管理集成验收

日期：2026-10-12。里程碑：M1 · 手动学习事务管理；目标版本：`v0.1.0`。目标集成分支：`develop`（经发布 PR 合并进 `main`）。

本记录覆盖 M1 全部三组（E10 持久化与课程、E11 手动任务与时间约束、E12 基础业务前端），作为里程碑验收与 `v0.1.0` 发布依据。E10 有独立记录 [E10-integration-acceptance.md](E10-integration-acceptance.md)，本文件在其之上补齐 E11／E12 与全组门槛对照。

## 验收门槛与对照

ROADMAP 中 M1 完成条件：**真实 PostgreSQL 复验；身份和空间授权；前后端闭环；日期、时区、状态正确**。

| 门槛 | 主要证据 |
| --- | --- |
| 真实 PostgreSQL 复验 | CI `PostgreSQL tests and Ruff`（`pgvector/pgvector:pg17`，执行 `alembic upgrade head` 与全量 pytest）；W003 迁移升级／回退复验；本机专用库全量 pytest |
| 身份和空间授权 | W004 单用户会话、W005 默认空间注入、W006 对象／关联归属（`test_auth_api.py`、`test_workspace_default.py`、`test_workspace_scope.py`、`test_app.py` 就绪检查）；端到端覆盖未登录跳转与退出后再拒 |
| 前后端闭环 | 端到端 `frontend/tests/e2e/manual-management.spec.ts`：登录 → 学期 → 课程 → 任务（日期精度 DDL、剩余耗时）→ 固定日程 → 进度与原因 → 设置（容量／时区／可用时段）→ 刷新持久化与时区换算 → 接口失败提示 → 归档／历史／恢复 → 退出后受保护路由 |
| 日期、时区、状态正确 | W008 空间时区转换、W009 归档查询、T003 日期精度、T007/T008/T009 目标日期／逾期／剩余耗时、T010 学期与截止筛选、T011 每周日程展开、T012 可用时间重叠与跨日 |

## 子项证据

### E10 持久化、工作空间与课程（#5）

聚合于父级 PR #291；逐项证据见 [E10-integration-acceptance.md](E10-integration-acceptance.md)。

| 子 Issue | 交付与来源 |
| --- | --- |
| #32 W001 | 基础 PostgreSQL 模型、迁移与 Compose（PR #1） |
| #46 W002 | 学期、课程 API（PR #1） |
| #47 W003 | 基础迁移升级／回退复验（W003-migration-recheck.md） |
| #48 W004 | 单用户身份校验与会话退出 |
| #49 W005 | 当前工作空间注入应用用例（PR #274／#277） |
| #50 W006 | 对象读取／修改／关联空间授权（PR #275） |
| #51 W007 | 个人时区与每周学习容量设置（PR #288） |
| #52 W008 | 日期时间转换使用工作空间时区（PR #289） |
| #53 W009 | 学期归档后的查询规则（PR #290） |
| #56 W010 | PATCH 必填字段置空修复（PR #272） |
| #57 W011 | 数据库就绪检查（PR #273） |

### E11 手动任务与时间约束（#6）

叶子 PR 均以集成分支 `issue/#6` 为目标，父级 PR #320 合并进 `develop`。

| 子 Issue | 交付与来源 |
| --- | --- |
| #54 T001 / #55 T002 | 手动任务、固定日程与可用时间 API（PR #1 补录） |
| #58 T003 | 接口集成与日期精度复验（PR #310；T003-api-recheck.md） |
| #59 T004 | 领域测试脱离数据库（PR #311） |
| #60 T005 | 测试库目标保护与配置隔离（PR #312） |
| #61 T006 | 进度变更历史与调整原因（PR #313） |
| #64 T009 | 剩余耗时维护（PR #314） |
| #62 T007 | 个人目标日期与正式 DDL 区分（PR #315；迁移 `t007_task_target_due_source`） |
| #63 T008 | 逾期标记派生（PR #316） |
| #65 T010 | 学期与截止区间筛选（PR #317） |
| #66 T011 | 每周日程范围内展开（PR #318） |
| #67 T012 | 可用时间重叠与跨日规则（PR #319） |

### E12 基础业务前端（#7）

聚合于父级 PR #261。

| 子 Issue | 交付 |
| --- | --- |
| #68 U001 | 业务页面导航与路由 |
| #69 U002 | 统一 API 客户端与错误展示 |
| #70 U003 | 登录退出与受保护页面 |
| #71 U004 | 学期列表创建与归档恢复界面 |
| #72 U005 | 课程列表与编辑表单 |
| #73 U006 | 任务列表与手动编辑表单 |
| #74 U007 | 任务进度与调整记录界面 |
| #75 U008 | 固定日程录入与周视图 |
| #76 U009 | 时区、可用时间与学习偏好设置页 |
| #77 U010 | 手动管理端到端流程验收 |

## 本机集成结果

环境：macOS（aarch64），Python 3.12.13，uv 锁定依赖，本机 Homebrew PostgreSQL **16.14**。项目基线 PostgreSQL 17 的验证由完整 CI 提供，不将本机版本混写成 17。

本次新建专用库 `campusflow_m1_test`；默认 `campusflow_test` 含其他集成分支迁移，未清空或修改；开发库未参与测试。

```bash
cd backend
CAMPUSFLOW_TEST_DATABASE_URL=postgresql+psycopg://campusflow:campusflow@127.0.0.1:5432/campusflow_m1_test uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
cd ../frontend
npm run build
npm run test:e2e   # 需真实 API 与全新数据库
```

结果：**87 tests passed**；Ruff lint／format 通过；前端类型检查与生产构建通过；Chromium 端到端流程在全新数据库上通过（约 2.5s）。

`npm run test:e2e` 依赖空数据库（用例断言初始无进行中学期），CI 每次使用全新 `campusflow_test`，本机以其为一次性库执行。

## CI 与浏览器证据

- E11 叶子 PR #313–#319 与父级 PR #320：每项最新提交的 `PostgreSQL tests and Ruff`、`Locked install and type build`、`Manual management browser flow` 全部通过。
- 修复 PR #322（见下）三项检查全部通过。
- CI 后端作业使用 `pgvector/pgvector:pg17` 执行 `alembic upgrade head` 与全量 pytest；前端浏览器作业在同一 PostgreSQL 17 服务上启动真实 API 并以 Chromium 运行手动管理流程。没有跳过失败检查。

## 验收中发现的缺陷与去向

- **#321 设置页竞态**：初始加载完成前表单即可编辑，异步 `Object.assign(form, settings)` 会覆盖用户输入（本地 `npm run test:e2e` 稳定复现于 `manual-management.spec.ts:79`，插入 1.5s 等待即通过）。已由 **PR #322** 修复（加载完成前不渲染表单），本地与 CI 均通过，Issue 已关闭。

## 限制、未实现范围与授权

- 仍为本机单用户默认空间；对象／关联归属已校验，但**不是**生产多账户身份或用户空间绑定；进程内会话、服务重启失效；默认凭据未替换；生产会话存储与共享部署安全待后续完成。
- 本里程碑交付手动学习事务管理闭环（学期、课程、任务、固定日程、可用时间与个人设置）。资料导入、事项核对、带引用问答、两周计划与冲突检查属 M2–M5，尚未实现，本记录不宣称具备。
- 既有 Starlette TestClient httpx 与 Alembic path_separator 弃用警告不影响通过结果，后续依赖升级时处理。
- 本记录初次验收后，发布 PR #325 复审另发现三处缺陷，见下方 #327 复验。初次全绿结果未覆盖这些边界，不能代替修复后的最新检查。
- 用户在本次对话明确允许在验证／完整 CI 通过后由维护者直接合并本批 PR，并将 `develop` 经发布 PR 合并进 `main`，作为非作者评审要求的本次显式例外。不伪造独立批准，不使用 admin 绕过 `main` 保护。

## 发布复审修复（#327）

2026-10-12 对发布提交 `e33b523` 复审，建立独立 Issue [#327](https://github.com/LUNARKN1GHT/CampusFlow/issues/327)，以 `issue/#327` → `develop` 交付：

- 跨日／跨周日程按工作空间日期展示每日占用片段；结束于午夜不占用下一天。修复前周日 23:00 至周一 01:00 在新周视图漏显，修复后周一显示 00:00–01:00；多日中间日显示 00:00–24:00。底层事件时间点不改变。
- 任务页接入 `target_date`、`due_source`、`due_note` 和 `overdue`。个人目标编辑／清空、来源说明保存与刷新保持一致；修改个人目标不改变正式 DDL；受阻任务同时显示逾期，完成后逾期消失。手动录入来源类型不冒充系统核验原文。
- 登录凭据以 UTF-8 字节执行恒定时间比较；中文错误输入返回 401，正确中文配置凭据可登录，退出后会话失效。

本机复验仍使用 PostgreSQL **16.14**：pytest 专用库 `campusflow_pr325_review_test`，浏览器全新库 `campusflow_issue327_e2e_test`。**91 tests passed**（新增 4 个中文认证案例）、Ruff lint／format、前端类型构建和 **3 项真实 API Chromium 流程**通过。浏览器新增案例使用洛杉矶浏览器时区与东京工作空间，覆盖每周跨周实例、多日、午夜结束和目标／逾期闭环。

本机默认 8000 端口已有服务，隔离 API 使用 8011，临时 Vite 配置只将 `/api` 代理至该端口；未停止已有服务、未修改仓库代理配置、未清空开发库。首轮新增浏览器测试发现选择框可访问名称与原始事件时间偏移断言问题，修正后重跑通过；时间点以 UTC 比较，不把响应偏移差异当成时间点变化。

修复 PR 及发布 PR #325 须分别以更新后的最新提交完整 CI 成功为依据；本机结果不替代 PostgreSQL 17 CI。组长在本次修复对话明确表示可直接通过，维护者可在完整 CI 通过后合并修复 PR；main 的现有保护要求仍须满足。
