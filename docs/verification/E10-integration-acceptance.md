# E10 持久化、工作空间与课程集成验收

日期：2026-10-09。父 Issue：#5；目标：`issue/#5` → `develop`。本记录覆盖 E10，不代表 M1 全组或 MVP 验收完成。

## 子项证据

| 子 Issue | 交付与来源 | 汇总核对 |
| --- | --- | --- |
| #32 W001 | PR #1 基础 PostgreSQL 模型、迁移与 Compose | 模型与独立迁移库表列／外键一致 |
| #46 W002 | PR #1 学期、课程 API | 当前全量接口回归通过 |
| #47 W003 | PR #254，W003-migration-recheck.md | 基础迁移证据保留，本次另验最新迁移 |
| #48 W004 | U003 单用户会话；Issue 中有非作者 PostgreSQL 17 复核 | 未登录／退出后 401，重启应用须重新登录 |
| #49 W005 | PR #274／#277 默认空间应用层注入 | 默认空间并发创建单例，API 不直查 ORM |
| #50 W006 | PR #275 对象／关联空间授权 | 读取、修改、关联及列表隔离回归 |
| #51 W007 | PR #288 时区与独立日／周容量 | 非法输入拒绝、持久化、旧客户端兼容 |
| #52 W008 | PR #289 空间时区转换 | 无偏移、显式偏移、跨日、DST、PATCH、周重复、前端编辑 |
| #53 W009 | PR #290 归档与历史查询 | 非删除归档、历史筛选、幂等和恢复 |
| #56 W010 | PR #272 必填字段 null 拒绝 | PATCH 拒绝与可空字段保留／清空回归 |
| #57 W011 | PR #273 独立数据库就绪检查 | 存活与就绪分开，数据库失败 503 且不泄密 |

三个新叶子任务均先经 PR 合并到父分支，没有直接进入 develop。父级 PR 合并前再次核对 GitHub 原生子 Issue 全部关闭。

## 本机集成结果

环境：macOS，Python 3.12.13，uv 锁定依赖，Homebrew PostgreSQL 16.14。项目基线 PostgreSQL 17 的验证由完整 CI 提供，不将本机版本混写成 17。

本次新建两个专用库：`campusflow_e10_test`（pytest），`campusflow_e10_migration_test`（迁移复验）。默认 `campusflow_test` 含其他集成分支迁移，未清空或修改它；开发库未参与测试。

```bash
cd backend
CAMPUSFLOW_TEST_DATABASE_URL=postgresql+psycopg://campusflow:campusflow@127.0.0.1:5432/campusflow_e10_test uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
cd ../frontend
npm run build
cd ..
git diff --check
```

本机结果：**65 tests passed**、Ruff lint／format、前端类型检查与构建、空白检查通过。使用 `UV_CACHE_DIR=/tmp/campusflow-uv-cache` 只改变本机缓存位置，不改变依赖锁。

`backend/tests/test_e10_integration.py` 作为汇总 API 演示：配置纽约时区和每周 900 分钟 → 创建学期／课程／日期精度 DDL 任务与跨日日程 → 归档隐藏学期入口但保留历史任务 → 新应用实例先拒绝未登录，再读取相同默认空间和设置 → 历史按学期查询及本地日期展开 → 恢复学期且任务不变 → 退出后再次拒绝读取。空间隔离与 DST 等边界由配套测试单独覆盖。

## 最新迁移复验

只在上述新建迁移库执行，以 `CAMPUSFLOW_DATABASE_URL` 指向该库：

1. 空库 `alembic upgrade w005_workspace_default`，插入旧配置：UTC、每日 180、休息 20、缓冲 45。
2. `alembic upgrade head`：周容量初始化为 **1260**，其他设置不变。
3. `alembic downgrade w005_workspace_default`：周容量列移除，旧配置保留。
4. `alembic upgrade head`：成功，周容量再次初始化为 1260。
5. 在该迁移库执行 `alembic downgrade base` 与 `alembic upgrade head`：完整回退／空库升级通过。此步骤只删除本次合成迁移夹具，不涉及真实数据。
6. SQLAlchemy inspector 对比当前 ORM metadata：七张业务表、全部列名与全部外键集合一致；唯一的额外表为 `alembic_version`。

回退 W007 会移除周容量值，这是迁移回退的预期行为；再次升级按每日容量重新初始化，不宣称保留已回退的独立周容量。

## CI 与浏览器证据

[PR #288](https://github.com/LUNARKN1GHT/CampusFlow/pull/288)、[PR #289](https://github.com/LUNARKN1GHT/CampusFlow/pull/289)、[PR #290](https://github.com/LUNARKN1GHT/CampusFlow/pull/290) 的最新提交完整 CI 均通过：PostgreSQL 17 tests and Ruff、锁定安装与类型构建、真实 Chromium 手动管理流程。浏览器新增验证周容量刷新持久化、东京时区展示／编辑不移位、归档入口、历史课程／任务查询和恢复。

PR #290 首次浏览器回归失败于测试标签精确匹配（未找到学期下拉框）；改为限定筛选栏的 combobox 后，最新提交的 push 与 PR 两套浏览器 CI 都通过。没有跳过失败检查。父级 PR 仍须以自身最新提交的同组三项 CI 成功为最终合并／关闭依据。

## 限制、后续任务与授权

- 本机默认单用户空间的对象归属保护已实现，不是生产多账户身份／用户空间绑定；默认凭据替换、生产会话存储和共享部署安全继续按既有规划推进。
- T004／T005 继续负责领域测试脱离数据库及测试目标保护；本次以明确专用库执行，未顺带宣称这两项完成。
- 资料模块未在 E10 实现；归档说明仅定义后续接入应遵循的非删除语义。资料、问答、排期与其他集成组仍独立验收。
- 现有 Starlette TestClient httpx 与 Alembic path_separator 弃用警告不影响通过结果，后续依赖升级时处理。本组未发现阻塞集成的开放缺陷。
- 用户在本次对话明确允许三个新叶子 PR 及本父级 PR 在验证／完整 CI 通过后由维护者直接合并，作为非作者评审要求的本次显式例外。不伪造独立批准，不使用 admin 绕过 main 保护。
