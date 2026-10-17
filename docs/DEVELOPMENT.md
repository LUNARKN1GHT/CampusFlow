# 本地开发

当前代码包括 Vue 手动管理业务页面、本机单用户登录、Python API、OpenAPI，以及学期／课程／手动任务与进度历史／固定日程周视图／可用时间／学习偏好接口。业务数据接入 PostgreSQL，调用前需启动数据库并执行迁移；健康检查表示进程存活，就绪检查验证数据库连通。Compose 同时提供 pgvector 扩展和 Redis，RQ Worker 已在资料整合分支接入；向量检索尚未实现。技术边界见 [架构文档](ARCHITECTURE.md)，独立复验和后续开发任务见 [开发路线](planning/ROADMAP.md)。

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
- 就绪检查：<http://127.0.0.1:8000/api/v1/ready>
- Swagger UI：<http://127.0.0.1:8000/docs>
- OpenAPI：<http://127.0.0.1:8000/openapi.json>

业务接口需要本机单用户会话。默认用户名为 `student`、默认密码为 `campusflow-dev`；仅供本机开发，复制 `.env.example` 后可通过 `CAMPUSFLOW_LOCAL_USERNAME` 和 `CAMPUSFLOW_LOCAL_PASSWORD` 修改。浏览器登录后使用 HttpOnly Cookie 保存会话，退出或后端重启后旧会话失效。共享部署前不得继续使用默认凭据。

健康检查 `/api/v1/health` 成功响应为 `{"status":"ok","service":"campusflow-api"}`，仅验证 API 进程存活。就绪检查 `/api/v1/ready` 以短超时连接数据库并执行 `SELECT 1`，数据库不可达时返回 503 且不包含连接信息或口令。已注册的业务路径包括 `/api/v1/semesters`、`/courses`、`/tasks`、`/fixed-events` 和 `/availability-slots`（后四项同样位于 `/api/v1` 下），具体方法和字段见 OpenAPI。

## 资料后台与诊断

扩展导入：DOCX 已接通校验与 Worker 正文解析。N005 新增网页快照迁移，启动前执行 `uv run --locked alembic upgrade head`；受保护接口为 `POST /api/v1/materials/web`。请求字段、失败处理和原文读取见 [网页快照说明](verification/N005-web-snapshot.md)。N003/N006 新增 python-docx 与 Beautiful Soup，更新后执行 `uv sync --locked`，Docker Worker 需按原说明重新构建并启动，不能继续使用旧镜像。N006 使用保存的响应编码读取网页正文，不需要新增模型密钥；现有 Worker 的视觉识别配置要求不变。位置解释和未覆盖范围见 [网页解析说明](verification/N006-web-parsing.md)。

资料处理后端与 RQ Worker 已在本整合分支接入。Windows 使用 Docker 运行 Linux
Worker；API 与 Worker 共用数据库、Redis 和私有文件目录。完整启动命令、三种输入
验证、只读诊断和安全重试步骤见 [资料处理运行手册](MATERIAL_PROCESSING.md)。
资料库页面 D019—D021 已接入导入、原文定位、归档与确认删除；真实队列浏览器回归见 [M2 集成验收](verification/M2-integration-acceptance.md)。

```powershell
# 仓库根目录，先按手册填写 backend/.env 并升级数据库
docker compose -f infra/docker-compose.worker.yml up --build -d
# backend/ 目录，输出不包含密钥、正文或连接口令
uv run --locked python -m campusflow.workers.diagnostics
```

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

X001 新增候选、字段和证据表，更新代码后先运行 `uv run --locked alembic upgrade head`。该基础供后续自动提取与核对使用，尚未开放候选写入 HTTP 接口。来源、删除边界和验证命令见 [X001 说明](verification/X001-candidate-evidence.md)。

X003 复用 X002 装配出的 TextGenerator，应用调用 `task_extraction.extract_task_drafts` 得到已校验草稿，再按需显式调用 `candidates.save_candidates`。尚无提取按钮或自动排队入口。专项测试命令：`uv run --locked pytest tests/test_task_extraction.py tests/test_deadline_completeness.py -q`；字段、日期检查范围及样本证据见 [X003 说明](verification/X003-progress.md)。

X004 复用现有文字模型配置，通过 `event_extraction.extract_event_drafts` 显式取得活动候选；调用方可用 `candidates.save_candidates` 保存待核对数据。无新增安装包或迁移，没有自动提取按钮。专项测试：`uv run --locked pytest tests/test_event_extraction.py -q`；错误处理与契约见 [X004 说明](verification/X004-event-extraction.md)。

R002 追加字段修正记录表；更新代码后需运行 `uv run --locked alembic upgrade head`。应用层通过 `candidates.revise_candidate_field` 追加修正，原始提取与证据不改写，仍不开放候选写入 HTTP 接口。专项测试：`uv run --locked pytest tests/test_candidate_revisions.py -q`；契约与边界见 [R002 说明](verification/R002-field-revisions.md)。

R003 追加核对状态变更记录表；同样需先 `uv run --locked alembic upgrade head`。状态转换与确认前必须字段检查在应用层 `candidates.set_candidate_review_status`，仍不开放候选 HTTP 接口。专项测试：`uv run --locked pytest tests/test_candidate_review.py -q`；转换集合与确认规则见 [R003 说明](verification/R003-review-transitions.md)。

R004 给 `tasks` 增加来源候选关联列；更新代码后需运行 `uv run --locked alembic upgrade head`。应用层 `conversion.convert_candidate_to_task` 把已确认的任务候选转为正式任务，幂等且只解析代码可识别的日期写法。专项测试：`uv run --locked pytest tests/test_candidate_conversion.py -q`；映射与日期精度见 [R004 说明](verification/R004-task-conversion.md)。

R005 给 `fixed_events` 增加来源候选关联列；同样需先 `uv run --locked alembic upgrade head`。应用层 `conversion.convert_candidate_to_event` 把已确认的日程候选转为固定日程，起止不可确定时拒绝。专项测试：`uv run --locked pytest tests/test_candidate_event_conversion.py -q`；时间解析与校验见 [R005 说明](verification/R005-event-conversion.md)。

R006 新增 `course_rules` 表；更新代码后需运行 `uv run --locked alembic upgrade head`。应用层 `conversion.convert_candidate_to_rule` 把已确认的规则候选转为正式课程规则，幂等且只保留原文不做日期换算；`GET /api/v1/rules` 按课程／学期只读查询。专项测试：`uv run --locked pytest tests/test_candidate_rule_conversion.py -q`；字段映射与删除留痕见 [R006 说明](verification/R006-confirmed-rules.md)。

R007 给 `fixed_events` 补齐来源失效留痕列；更新代码后需运行 `uv run --locked alembic upgrade head`。来源详情接口 `GET /api/v1/tasks/{id}/source` 与 `GET /api/v1/fixed-events/{id}/source` 返回出处、引用与可验证状态，资料删除后不再返回可验证引用。专项测试：`uv run --locked pytest tests/test_source_detail.py -q`；结构与失效行为见 [R007 说明](verification/R007-source-detail.md)。
X005 复用相同文字模型配置，通过 `rule_extraction.extract_rule_drafts` 得到待核对规则，按需显式保存。无新增依赖、迁移和自动入口。专项测试：`uv run --locked pytest tests/test_rule_extraction.py -q`；完整原文及条件字段契约见 [X005 说明](verification/X005-rule-extraction.md)。

X002 文字生成复用私有 `ZHIPU_API_KEY`，型号与视觉识别独立：`CAMPUSFLOW_TEXT_MODEL=glm-4-flash-250414`、`CAMPUSFLOW_TEXT_TIMEOUT_SECONDS=60`、`CAMPUSFLOW_TEXT_MAX_TOKENS=4096`。等待设置是网络连接/读取等待上限（最长 120 秒），不是整个多次读取的总时限；单次生成长度可设置 1–8192。配置在 `backend/.env.example`，真实密钥只写本地环境，不提交。装配入口 `workers.generation.build_text_generator` 缺密钥时拒绝创建真实适配器；启动及普通测试不会自动调用模型。X002 尚无提取按钮或自动 Worker 任务，后续业务接入时才使用此入口。测试和调用契约见 [X002 说明](verification/X002-generation-contract.md)。

X007 的 `course_matching.match_candidate_course` 接受所选学期及带证据的课程/班级原文字段，返回待核对匹配建议，不修改候选 scope。调用方按需显式使用 `save_candidates` 保存；无新增依赖或配置。专项测试：`uv run --locked pytest tests/test_course_matching.py -q`；契约见 [X007 说明](verification/X007-course-matching.md)。

X006 的 `relative_dates.resolve_task_deadline` 为 X003 草稿附加日期换算结果，不额外调用模型。需显式提供当前版本内的依据片段及来源时区，输出再按需调用 `save_candidates` 保存。无新增配置或依赖；专项测试为 `uv run --locked pytest tests/test_relative_dates.py -q`，输入和限制见 [X006 说明](verification/X006-relative-dates.md)。

表结构由 Alembic 迁移管理，在 `backend/` 目录执行：

```bash
uv run --locked alembic upgrade head                       # 升级到最新结构
uv run --locked alembic revision --autogenerate -m "说明"   # 按模型变更生成新迁移（需人工检查）
uv run --locked alembic downgrade -1                       # 回退一步
```

测试库（`campusflow_test`）的迁移由 pytest 的 `tests/conftest.py` 自动执行，无需手动处理。

当前测试夹具在每个用例前清空相关业务表；只能使用专用测试库，不把 `CAMPUSFLOW_TEST_DATABASE_URL` 指向有实际数据的数据库。T004、T005 已接入：纯领域测试不触发数据库，测试库名必须以 `_test` 结尾。

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

## Q001 离线评测准备

在仓库根目录执行 `python scripts/evaluate_qa_preparation.py --output-dir data/q001/run-001`，生成逐题证据 JSON 和 B017 格式的 CSV。仅使用 Python 标准库，无需启动数据库或设置模型密钥；每次用新目录保留历史。预设回答只检查计数流程，不代表供应商效果，详见 [Q001 准备说明](verification/Q001-preparation.md)。

真实比较入口 `python scripts/evaluate_qa_live.py` 默认不联网。用户授权的型号、费用上限、显式执行／继续运行参数及恢复点见 [Q001 真实评测进度](verification/Q001-live-progress.md)。继续运行不能重置预算，也不能覆盖已有人工复核记录。

## Q003 检索分块

Q003 不增加迁移或依赖；`build_version_blocks` 通过既有仓储读取指定版本，纯计算生成检索块，不联网。默认单块 3000 UTF-8 字节、无重叠，可在内部调用显式传入 `ChunkingConfig`；仍须与后续向量模型输入上限一致。专项检查：`uv run --locked pytest tests/test_retrieval_blocks.py`，细节见 [Q003 说明](verification/Q003-source-blocks.md)。

## 当前限制

Q002 新增向量配置表，启动新代码前在 `backend/` 执行 `uv run --locked alembic upgrade head`。环境字段见 `.env.example` 的 `CAMPUSFLOW_EMBEDDING_*`；沿用 `ZHIPU_API_KEY`，不要写入配置记录。构造适配器不会联网，现有 Worker 尚未自动调用；契约和版本升级要求见 [Q002 说明](verification/Q002-embedding-adapter.md)。

- 手动管理前后端与本机单用户会话已实现；本分支已接入空间授权、上传与 Worker；资料页面闭环已有本地真实队列证据；暂无生产身份系统、核对、RAG 和排期实现。
- PR #1 正文报告 22 个测试通过；本次规划未在当前机器独立复验。应以实际执行记录为准，W003、T003 跟踪迁移与接口复验。
- `npm run preview` 只预览构建后的静态页面，未配置生产 API 反向代理；完整联调使用 `npm run dev`。
- 开发服务仅绑定本机地址，当前不用于公网部署。后续共享部署需要认证、访问范围控制与反向代理配置。
- Swagger UI 使用外部静态资源；离线时 UI 可能无法加载，可直接访问 `/openapi.json` 查看接口描述。
- 项目代码和项目自有文档采用 [Apache License 2.0](../LICENSE)；第三方材料仍需分别核对并遵循其自身授权条件。
