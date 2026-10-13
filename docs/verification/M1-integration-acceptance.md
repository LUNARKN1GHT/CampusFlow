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
- M1 未发现其他阻塞集成的开放缺陷；上表缺陷已修复关闭。
- 用户在本次对话明确允许在验证／完整 CI 通过后由维护者直接合并本批 PR，并将 `develop` 经发布 PR 合并进 `main`，作为非作者评审要求的本次显式例外。不伪造独立批准，不使用 admin 绕过 `main` 保护。
