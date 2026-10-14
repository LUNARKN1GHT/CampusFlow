# CampusFlow 技术方案与项目架构

本文确定首期开发基线，业务范围以 [README](../README.md) 和 [AGENTS](../AGENTS.md) 为准。**当前已实现工程骨架、本机单用户会话、PostgreSQL 迁移，以及学期／课程／手动任务与进度历史／固定日程／可用时间／学习偏好的前后端闭环；本整合分支已接入跨工作空间授权、资料上传/解析/定位及 RQ Worker；事项核对、问答与排期尚待实现。** 已合并实现的独立复验与后续任务见 [开发路线](planning/ROADMAP.md)，不以代码存在代替验收通过。

## 1. 总体决策

N005 通过应用用例调用抓取端口，再将原始网页和快照来源信息分别保存到私有文件存储、资料版本记录。API 入口必须先校验会话与空间；不提供公开代理。网页原文按附件返回，不能在应用同源执行。N003 Word 使用 python-docx；N006 网页正文使用用户确认的 Beautiful Soup，经统一 ParserRegistry 接入 Worker，响应编码取自版本来源元数据。网页定位值记录清洗前同类标签的序号（如 p:3），与版本 ID 共同定位原文，正文提取不再次联网。细节见 [N006 说明](verification/N006-web-parsing.md)。

N004 的公开网页抓取使用 Python 标准库：应用层定义抓取结果与错误契约，基础设施实现地址检查、连接固定、逐跳检查及大小和时间上限；未新增外部依赖或公开 HTTP 接口。详细边界见 [N004 说明](verification/N004-public-web-fetch.md)，后续网页导入用例须先执行用户与工作空间授权。

采用单仓库、前后端分离、模块化单体架构。前端提供交互，Python 后端负责业务规则、资料处理与模型编排；耗时工作通过同一个后端包的独立 Worker 进程执行。

四人小组首期需要集中完成业务闭环，因此保持一套后端代码和一套数据模型。API 与 Worker 是不同进程，共享应用层用例，不单独维护微服务接口。仅为已经出现的业务边界增加抽象。

### 技术选型

| 层面 | 选择 | 原因与引入时机 |
| --- | --- | --- |
| 前端 | Vue 3 + TypeScript + Vite | 页面、组件与 API 调用分开，类型检查帮助维护接口；本次建立最小页面。多页时加 Vue Router，有共享状态需求时加 Pinia |
| Python 运行环境 | Python 3.12 + uv | 小组统一解释器小版本，使用 `pyproject.toml` 和 `uv.lock` 管理依赖；本次落地 |
| HTTP API | FastAPI + Uvicorn | Pydantic 校验、OpenAPI 与路由拆分；已注册健康及手动管理接口 |
| 配置 | pydantic-settings | 环境变量与本地 `.env` 注入，配置集中定义；本次落地 |
| 持久化 | PostgreSQL 17 + SQLAlchemy 2 + psycopg 3 + Alembic | 已接入六张基础业务表及迁移；资料、引用和计划模型随功能扩展。默认同步 Session，HTTP 同步数据库路径使用 `def`，不得在 `async def` 中直接执行阻塞 I/O |
| 向量检索 | PostgreSQL 的 pgvector 扩展 | Compose 初始化已启用扩展；向量模型、索引及实际检索在 RAG 阶段实现 |
| 文件存储 | 后端私有本地目录 + 存储接口 | 单实例开发先降低运维成本；文件元数据进数据库，原文件不进 Git。需要多实例时替换为 S3 兼容存储 |
| 资料解析 | pypdf + 可替换 OCR／多模态适配器 | PDF 文本优先直接提取；扫描件和复杂表格走识别流程，保留页码及定位；资料功能阶段引入 |
| AI 与 RAG | 应用层显式流程 + 模型适配接口 | 区分生成、多模态识别和 embedding 能力。具体供应商与模型待授权样本评估、费用和可用性验证后确定，不让 SDK 类型进入业务层 |
| 后台作业 | RQ + Redis 7 + Python Worker | Compose 已提供 Redis；RQ、Worker 和实际作业在资料处理阶段接入。业务状态以 PostgreSQL 为准，Redis 不作为事实来源 |
| 质量检查 | pytest + HTTPX + Ruff；vue-tsc + Vite build | 后端接口和规则测试、格式与静态检查，前端类型检查和构建；本次提供可执行命令 |
| 部署 | 开发时前后端双进程 + 数据库和 Redis Compose | 后续增加 Worker 与同源反向代理；当前没有公网生产部署或认证实现 |

Python 版本写在 `backend/.python-version`，前端建议 Node 24（见 `.nvmrc`，最低 22.12）。应用依赖的精确版本以实际生成的 `uv.lock`、`package-lock.json` 为准，不手工修改锁文件。

### 暂不引入的组件

- Django 管理后台、微服务、Kubernetes：当前主要需求是学生侧 API 和资料处理，暂时没有相应运维或后台管理需求。
- LangChain／LangGraph 等编排框架：先把检索、引用校验和计划确认实现为明确的 Python 用例，需要复杂持久工作流时再评估。
- 独立向量数据库、Elasticsearch：先验证 PostgreSQL + pgvector 的效果；中文关键词检索与混合排序必须用中文样本评估，不假定默认全文检索配置足够。
- 本地大模型／GPU 部署：模型适配接口保留替换能力，首期不要求每个成员配置 GPU。
- UI 组件库、完整日历组件：具体页面原型确定后再引入，当前只有环境联通页。

## 2. 系统架构图

下图保留基础管理链路示意；资料后端的实际启动与装配以 [资料处理运行手册](MATERIAL_PROCESSING.md) 为准。实线表示基础调用链，虚线表示完整资料到事实的后续链路；不表示所有链路已完成阶段验收。浏览器已通过统一 API 客户端接通手动管理业务，并由 Playwright 在真实 PostgreSQL 上覆盖登录、创建、更新、刷新、错误重试和退出保护流程。

```mermaid
flowchart TB
    User[学生浏览器] --> Web[Vue 3 + TypeScript]
    Web --> Proxy[Vite 开发代理 /api]
    Proxy --> API[FastAPI 路由 /api/v1]
    API --> Health[进程健康检查]
    API --> Ready[数据库就绪检查]

    API --> UseCases[应用层：课程、任务、固定日程]
    UseCases --> Domain[领域层：日期、状态与时间范围]
    UseCases --> Adapters[基础设施仓储]
    Adapters --> DB[(PostgreSQL)]
    API -.-> Future[待实现：资料、核对、问答与计划]
    Future -.-> Adapters
    Adapters -.-> Files[(私有原文件存储)]
    Adapters -.-> Queue[(Redis / RQ)]
    Adapters -.-> Models[模型服务：生成、视觉、embedding]
    Queue -.-> Worker[独立 Python Worker]
    Worker -.-> UseCases
```

开发前端通过 Vite 将 `/api` 转发到 `127.0.0.1:8000`，无需开放通配 CORS。生产目标由同一个域名提供前端静态文件和 `/api`，由反向代理替代 Vite。API、Worker、数据库和文件存储不直接暴露给浏览器。

健康接口 `/api/v1/health` 仅表示 API 进程存活；就绪接口 `/api/v1/ready` 以短超时连接检查数据库连通，不可达时返回 503 且不泄露连接信息或口令。两者都不宣称模型等尚未接入的能力可用。

## 3. 文件分层

以下目录均已建立；标注“预留”的目录目前只包含职责说明，按真实用例逐步添加代码。

```text
CampusFlow/
├── README.md                    # 对外项目计划与开发入口
├── AGENTS.md                    # 业务及工程约定
├── CONTRIBUTING.md              # 小组贡献流程
├── .gitignore
├── backend/
│   ├── pyproject.toml           # Python 依赖与检查配置
│   ├── uv.lock                  # Python 精确依赖锁
│   ├── .python-version
│   ├── .env.example             # 可提交的配置示例
│   ├── src/campusflow/
│   │   ├── main.py              # 应用工厂、依赖装配、路由注册
│   │   ├── core/                # 配置与运行时通用设施
│   │   ├── api/                 # HTTP 路由与输入输出 schema
│   │   ├── application/         # 课程、任务、日程用例及仓储端口
│   │   ├── domain/              # 日期、状态和时间范围业务规则
│   │   ├── infrastructure/      # 数据库与仓储；其余适配器待实现
│   │   └── workers/             # 后台入口、安全日志与只读诊断
│   ├── migrations/             # Alembic 环境及初始六表迁移
│   └── tests/                  # 接口及后续领域、集成测试
├── frontend/
│   ├── package.json
│   ├── package-lock.json
│   ├── .nvmrc
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── index.html
│   └── src/
│       ├── main.ts              # 前端入口
│       ├── App.vue              # 应用根组件
│       ├── views/               # 页面
│       ├── components/          # 预留：跨页面共享组件
│       ├── api/                 # HTTP 调用与接口数据处理
│       └── styles.css
├── infra/                      # PostgreSQL、Redis Compose 与初始化脚本
└── docs/
    ├── ARCHITECTURE.md          # 本文：选型、架构、边界
    ├── DEVELOPMENT.md           # 环境与执行命令
    ├── GitLab.md                # 已有仓库连接说明
    └── planning/                # Milestone、Issue、标签与验收覆盖
```

### 后端依赖方向

```mermaid
flowchart LR
    Entrypoints[API / Worker] --> Application[application 用例与 ports]
    Application --> Domain[domain 纯业务规则]
    Infrastructure[infrastructure 具体实现] --> Application
    Main[main 装配入口] --> Entrypoints
    Main --> Infrastructure
```

- `api` 只处理 HTTP 校验、依赖注入、调用用例与响应转换，不直接查库或调用模型。
- 默认工作空间初始化已移至应用用例；对象读取、关联与作业手动补投都校验工作空间范围（W005/W006）。
- `application` 协调流程、事务与授权确认，依赖接口；按 `materials`、`qa`、`tasks`、`planning` 等业务组织文件。
- `domain` 不导入 FastAPI、ORM、供应商 SDK 或运行配置；日期与冲突规则可以脱离服务器独立测试。
- `infrastructure` 实现接口，SQLAlchemy 模型只在该层使用，不直接返回给前端。
- `main`／后续 Worker 启动模块负责组装具体依赖。健康检查无需经过空的 service 和 repository。
- 不预先创建十组空 CRUD 模块或通用基类；第一个实际用例确定后再增加必要文件。

前端页面只通过 `api/` 访问后端。跨页面路由与状态管理按需增加，后端返回的事实与执行结果为准，前端不能把演示数据当成真实任务。

## 4. 关键业务链路设计

### 资料到可查询事实（解析已接入，提取与索引待实现）

```mermaid
sequenceDiagram
    actor Student as 学生
    participant API as Python API
    participant DB as PostgreSQL
    participant Queue as Redis/RQ
    participant Worker as Python Worker
    participant Model as 解析器/模型适配器
    Student->>API: 上传资料并选择适用范围
    API->>DB: 保存资料版本、作业与待投递记录
    API-->>Student: 202 + material_id + job_id
    API->>Queue: 投递 job_id，失败可补偿
    Queue->>Worker: 领取任务
    Worker->>DB: 校验资料版本、状态与幂等键
    Worker->>Model: 解析、提取、分块与 embedding
    Model-->>Worker: 结构化候选结果和来源定位
    Worker->>DB: 保存片段、候选事项、向量与处理状态
    Student->>API: 查看结果，核对日期与适用范围
    API->>DB: 保存确认记录，建立正式任务
```

上传时校验大小、类型和访问范围，服务端生成存储键，不直接使用用户文件名作为路径。原文件、页码／区域、提取文本和版本要可关联；多个片段不能丢失自己的引用位置。

数据库与队列无法假定原子提交：业务作业和待投递记录在同一事务内保存，投递器重试未投递记录；Worker 接受重复消息但不重复产生结果。失败、部分成功、超时与重试次数应可见，删除或替换的资料版本不得被晚到的任务重新写回索引。

### 检索、回答与计划（待实现）

1. 按工作空间、学期、教学班、资料有效状态过滤，再进行检索；不能先检索全部用户资料后再由模型判断权限。
2. 片段带稳定引用 ID 和版本。模型生成回答后校验引用 ID 存在且在候选集内；仅有合法 ID 不等于语义支持充分，还需用样本评估支持程度。
3. Agent 输出查询／规划／冲突检查意图和结构化参数，后端校验后调用固定用例；任意生成文本不能变成执行命令。
4. 已确认任务进入确定性排期器；先满足固定时间和截止约束，再应用偏好。模型可以解释或建议拆分，不能越过约束验证。
5. 计划以带版本的草案保存；接受操作检查草案版本，过期草案需重新比较，避免覆盖较新的安排。
6. 第三阶段的外部日历适配器仅接受已确认的具体操作，并保存幂等键、外部事件 ID 和逐项结果。

## 5. 数据与接口边界

X007 的 domain/course_matching.py 负责严格名称/班级比较；application/course_matching.py 授权空间、学期、资料版本和证据，附加带原文的课程匹配建议。输出不修改 scope 或正式事项，复用 X001 字段/证据保存为 pending；候选快照不代替确认时的重新授权和范围核验。见 [X007 说明](verification/X007-course-matching.md)。

X001 已实现 ExtractedItem → ExtractedField → CandidateFieldEvidence 的分表关系。纯领域模型与校验位于 domain/candidates.py；应用用例先校验来源及空间，基础设施仓储承担持久化。API 与 Worker 使用同一个仓储装配入口。候选按来源版本级联清理，删除确认包含候选集合，模型输出不能直接变成已确认正式事项。具体契约见 [X001 说明](verification/X001-candidate-evidence.md)。

X004 在 application/event_extraction.py 定义活动字段并复用 X002 的授权与生成入口；domain/event_extraction.py 对活动描述及被引用原文语句做保守检查，拒绝提交截止或混合要求，缺失起止自动补核对原因。该入口只返回草稿，显式使用 X001 保存后仍 pending；不创建 FixedEvent，不换算日期。边界见 [X004 说明](verification/X004-event-extraction.md)。

X002 在 application/ports/generation.py 定义 TextGenerator，仅交换受控提示词、资料 JSON 与模型原始正文；供应商 HTTP 实现位于 infrastructure/llm/text.py，domain 不导入供应商库。application/candidate_generation.py 先检查空间与资料版本，再按调用用例定义的字段契约校验输出；编号必须属于本次输入，摘录必须逐字出现，整批有效才返回草稿，不在生成步骤写库。workers/generation.py 按配置装配，不随启动自动调用。后续 X003–X005 定义各业务字段，X009 继续负责专门的资料指令隔离验收；本项不替代真实语义准确率评测。限制与验证见 [X002 说明](verification/X002-generation-contract.md)。

X003 的 application/task_extraction.py 定义任务专用字段并复用 X002 授权与生成边界；domain/deadline_completeness.py 仅检查截止原文完整性，不依赖模型或系统时间，不生成可排期日期。缺失及建议原因由代码补充，模型不能覆盖。调用方显式使用 X001 保存草稿，仍保持 pending；日期换算及核对交互由后续模块负责，见 [X003 说明](verification/X003-progress.md)。

目标数据分组如下，具体字段和迁移在功能实现时确定：

| 分组 | 对象 | 约束重点 |
| --- | --- | --- |
| 学习背景 | Workspace、Semester、Course | 单用户首期仍保留工作空间边界，同名教学班分开 |
| 证据链 | Material、MaterialVersion、SourceChunk、Citation | 引用绑定版本与定位，原文件放存储，元数据与向量放数据库 |
| 事实与行动 | ExtractedItem、Rule、Task、Event | 核对状态与执行状态分开，DDL 与固定活动时间分开 |
| 计划 | PlanDraft、PlanVersion、TimeBlock、Conflict | 草案与接受状态、乐观版本检查、任务与时间块关联 |
| 后台作业 | ProcessingJob、Outbox | 幂等、重试、待投递补偿、资料失效检查 |
| 外部操作（第三阶段） | Confirmation、ExternalOperation | 确认范围、内容版本、幂等与实际结果 |

统一接口前缀为 `/api/v1`，使用 JSON 与 OpenAPI。未来上传使用 multipart；长任务返回 `202` 和作业 ID，首期通过轮询查看状态，不先引入 WebSocket。

时间采用带时区的 ISO 8601 表示，数据库时间点使用 `timestamptz`，另存原始文本与来源时区。只有日期而没有时刻的 DDL 保留日期精度，不伪装成午夜或 23:59 的确定时间。学校教学周需依赖适用校历换算。

当前业务错误通过 `DomainError` 和 `NotFoundError` 映射为可读消息及 HTTP 400／404，参数校验使用 422。后续完善统一稳定错误码与请求 ID；模型密钥只在后端配置，日志避免原始资料全文和个人信息。

当前实现仅限本机单用户会话：登录成功后颁发 HttpOnly Cookie，退出或服务重启使会话失效，业务路由要求有效会话。共享部署、多人账户和跨工作空间对象授权仍需 W004–W006 后续验收；“单用户”不等于可以把资料公开访问。

## 6. 实施顺序与完成条件

1. **M0–M1：** 工程骨架和首个持久化切片已合并；完成样本、CI、真实数据库复验、身份与空间授权、手动管理前端，形成可验收闭环。
2. **M2–M3：** 私有文件存储、RQ Worker、解析与来源定位、事项提取和核对；验证失败重试与删除派生失效。
3. **M4：** embedding、检索、模型适配器和带引用回答；用中文授权样本评估缺失信息和来源冲突。
4. **M5：** 两周计划、冲突检查和草案接受，完成八项业务 MVP 验收。
5. **M6–M9：** 按第二、第三阶段接入扩展导入、变更、依赖、提醒、导出、课程路径及外部日历。具体完成条件与叶子任务见 [开发路线](planning/ROADMAP.md)。

上述顺序细化 README 的三阶段功能边界。后续依赖在实际使用时加入锁文件，不为未实现能力预装整套 AI SDK。

## 7. 官方资料

- [Vue 快速开始](https://vuejs.org/guide/quick-start)、[TypeScript 支持](https://vuejs.org/guide/typescript/overview)、[Vite 入门](https://vite.dev/guide/)：前端工程基线。
- [FastAPI 多文件组织](https://fastapi.tiangolo.com/tutorial/bigger-applications/)：路由拆分；[后台任务边界](https://fastapi.tiangolo.com/tutorial/background-tasks/)：区分进程内任务与独立作业系统。
- [uv 项目管理](https://docs.astral.sh/uv/guides/projects/)：环境和依赖管理；[锁定与同步](https://docs.astral.sh/uv/concepts/projects/sync/)：团队安装使用 `--locked`。
- [SQLAlchemy ORM](https://docs.sqlalchemy.org/en/20/orm/quickstart.html)、[Alembic](https://alembic.sqlalchemy.org/en/latest/)、[pgvector](https://github.com/pgvector/pgvector)：关系数据、迁移和向量存储。
- [RQ](https://python-rq.org/)：Python 作业队列；[pypdf 文本提取](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)：解析能力及 OCR 边界。

## 资料处理运行补充（J009）

Windows 使用 Docker 运行现有 Linux RQ Worker，API 保持本机启动。两端共享同一数据库、Redis 库号和私有原文件目录。解析器注册表组合 PDF 文本解析、扫描页视觉回退与表格关联；数据库迁移分叉通过合并迁移汇合。只读诊断不调用模型，不修改任务，日志及 RQ 失败记录不保存原始异常堆栈。详见 [资料处理运行手册](MATERIAL_PROCESSING.md)。
