# 本地开发

当前代码包括 Vue 手动管理业务页面、本机单用户登录、Python API、OpenAPI，以及学期／课程／手动任务与进度历史／固定日程周视图／可用时间／学习偏好接口。业务数据接入 PostgreSQL，调用前需启动数据库并执行迁移；健康检查仍只表示进程存活。Compose 同时提供 pgvector 扩展和 Redis，向量检索与 RQ Worker 尚未接入。技术边界见 [架构文档](ARCHITECTURE.md)，独立复验和后续开发任务见 [开发路线](planning/ROADMAP.md)。

## 环境

- Python 3.12，由 `backend/.python-version` 指定；uv 可在同步时寻找或安装相应解释器。
- [uv](https://docs.astral.sh/uv/getting-started/installation/)。
- Node.js 建议 24，最低 22.12，使用 npm；版本提示见 `frontend/.nvmrc`。
- Docker Desktop（Windows 需开启 WSL2 后端）：用于本地 PostgreSQL + pgvector 与 Redis，见下一节。
- 首次安装依赖需要访问 Python 和 npm 包仓库。

## 本地基础设施（PostgreSQL 与 Redis）

数据库与 Redis 由 [infra/docker-compose.yml](../infra/docker-compose.yml) 定义，在 `infra/` 目录执行：

```bash
docker compose up -d        # 启动（首次自动拉取镜像）
docker compose ps           # 状态，两个服务应显示 healthy
docker compose down         # 停止（数据保留在命名卷中）
docker compose down -v      # 停止并清空数据（慎用）
```

连接信息（仅限本机开发）：`127.0.0.1:5432`（用户/密码/业务库均为 `campusflow`，测试库 `campusflow_test`，已启用 vector 扩展）与 `127.0.0.1:6379`（Redis）。连接字符串样例见 `backend/.env.example`。

故障排查：

- **国内网络拉取镜像失败/超时**：在 Docker Desktop 的引擎配置（Windows 上为 `%USERPROFILE%\.docker\daemon.json`）中配置 `registry-mirrors` 国内镜像加速地址后重启 Docker Desktop。修改前先退出 Docker Desktop，避免配置被覆盖。
- **WSL2 未启用**：管理员 PowerShell 执行 `wsl --install` 并按提示重启。
- **端口 5432/6379 被占用**：修改 compose 中对应端口映射后重新 `docker compose up -d`。
- **初始化脚本只执行一次**：修改 `infra/postgres/init/` 后需 `docker compose down -v` 再启动才会重新执行。

以下命令从仓库根目录打开终端执行，前后端分别占用一个终端。不需要手动激活 Python 虚拟环境。

## 启动后端

```bash
cd backend
uv sync --locked
uv run --locked alembic upgrade head
uv run --locked uvicorn campusflow.main:app --reload --host 127.0.0.1 --port 8000
```

先启动上述数据库，再执行迁移和后端启动命令。需要自定义时复制 `backend/.env.example` 为同目录 `.env`；支持应用名称、业务库、测试库与 Redis 地址配置，具体名称见样例。环境变量优先于 `.env`，不要把 `.env` 提交到仓库。

- 健康检查：<http://127.0.0.1:8000/api/v1/health>
- Swagger UI：<http://127.0.0.1:8000/docs>
- OpenAPI：<http://127.0.0.1:8000/openapi.json>

业务接口需要本机单用户会话。默认用户名为 `student`、默认密码为 `campusflow-dev`；仅供本机开发，复制 `.env.example` 后可通过 `CAMPUSFLOW_LOCAL_USERNAME` 和 `CAMPUSFLOW_LOCAL_PASSWORD` 修改。浏览器登录后使用 HttpOnly Cookie 保存会话，退出或后端重启后旧会话失效。共享部署前不得继续使用默认凭据。

健康检查成功响应为 `{"status":"ok","service":"campusflow-api"}`，仅验证 API 进程存活。已注册的业务路径包括 `/api/v1/semesters`、`/courses`、`/tasks`、`/fixed-events` 和 `/availability-slots`（后四项同样位于 `/api/v1` 下），具体方法和字段见 OpenAPI。数据库就绪检查尚待实现，健康接口成功不证明业务库可用。

## 启动前端

在另一个终端从仓库根目录运行：

```bash
cd frontend
npm ci
npm run dev
```

访问 <http://127.0.0.1:5173>。页面通过 Vite 代理调用真实后端，能显示检查中、连接成功、连接失败，并支持重试。5173 被占用时会直接报错，不自动切换端口。

前端请求统一使用相对 `/api` 地址。如调整后端端口，同步修改 `frontend/vite.config.ts` 的代理目标；不要把模型密钥写入前端环境变量。

## 数据库迁移

表结构由 Alembic 迁移管理，在 `backend/` 目录执行：

```bash
uv run --locked alembic upgrade head                       # 升级到最新结构
uv run --locked alembic revision --autogenerate -m "说明"   # 按模型变更生成新迁移（需人工检查）
uv run --locked alembic downgrade -1                       # 回退一步
```

测试库（`campusflow_test`）的迁移由 pytest 的 `tests/conftest.py` 自动执行，无需手动处理。

当前测试夹具在每个用例前清空六张业务表；只能使用专用测试库，不把 `CAMPUSFLOW_TEST_DATABASE_URL` 指向有实际数据的数据库。T004、T005 分别跟踪纯领域测试脱离数据库和测试目标保护，目前这些改进尚未合并。

## 验证

后端，在 `backend/` 下执行：

```bash
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
```

前端，在 `frontend/` 下执行：

```bash
npm run typecheck
npm run build
npm run test:e2e             # 需先启动 PostgreSQL、迁移和后端；自动启动 Vite
```

`build` 已包含类型检查。`test:e2e` 使用 Chromium 完成登录、学期、课程、任务、固定日程、进度、设置、刷新持久化、错误重试与退出保护流程；首次运行需在 `frontend/` 执行 `npx playwright install chromium`。CI 会启动真实 PostgreSQL 与后端后执行该流程。

仓库根目录可运行 `git diff --check` 检查空白问题。依赖变更必须同步锁文件：后端使用 `uv add`／`uv lock`，前端使用 `npm install`；日常安装使用 `uv sync --locked` 和 `npm ci`。

## 当前限制

- 手动管理前后端与本机单用户会话已实现；暂无生产身份系统、跨工作空间对象授权、Worker 命令、上传、核对、RAG 和排期实现。Redis 和 vector 扩展配置不等于相关业务已实现。
- PR #1 正文报告 22 个测试通过；本次规划未在当前机器独立复验。应以实际执行记录为准，W003、T003 跟踪迁移与接口复验。
- `npm run preview` 只预览构建后的静态页面，未配置生产 API 反向代理；完整联调使用 `npm run dev`。
- 开发服务仅绑定本机地址，当前不用于公网部署。后续共享部署需要认证、访问范围控制与反向代理配置。
- Swagger UI 使用外部静态资源；离线时 UI 可能无法加载，可直接访问 `/openapi.json` 查看接口描述。
- 项目代码和项目自有文档采用 [Apache License 2.0](../LICENSE)；第三方材料仍需分别核对并遵循其自身授权条件。
