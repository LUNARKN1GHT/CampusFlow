# 资料处理运行与查错（J009）

本手册对应资料后端整合分支：PDF／图片上传、粘贴文本、原文定位、RQ 后台处理、
失败查询与手动重试。**D019—D021 页面已接入并通过本地真实队列闭环验证；父组评审和 M2 确认仍待完成。**
复现与门槛证据见 [M2 集成验收](verification/M2-integration-acceptance.md)。
以下接口操作可通过后端 `/docs` 执行；界面设计稿不等同于已上线页面。

## 1. 各程序做什么

- API：接收上传、保存资料，告诉你资料编号和后台作业编号。
- PostgreSQL：保存资料、作业状态和原文片段，是查询状态的依据。
- Redis／RQ：保存等待执行的作业编号，不存资料原文。
- Worker：独立的后台程序，读取私有文件，解析后把片段和状态写回数据库。
- 粘贴文本目前直接解析并返回段落，不经过队列；PDF 和图片经 Worker 处理。

## 2. 首次启动（Windows + Docker Desktop）

先在仓库根目录启动数据库和 Redis：

```powershell
docker compose -f infra/docker-compose.yml up -d
```

在 `backend/` 中准备 API：

```powershell
uv sync --locked
# 尚无 .env 时才从 .env.example 复制；不要覆盖已经填写的本地配置。
uv run --locked alembic upgrade head
uv run --locked uvicorn campusflow.main:app --host 127.0.0.1 --port 8000
```

将视觉模型密钥写入 **backend/.env 的 ZHIPU_API_KEY**。当前模型为
`glm-4v-flash`，请求目标为智谱接口；图片及扫描 PDF 的页面会发送给该服务。
仅使用授权、脱敏或合成的资料。不要把密钥填进网页、命令参数或提交到 Git。
没有密钥时后台程序拒绝启动；诊断只检查是否填写，不验证账号余额或调用权限。

另开终端，在仓库根目录执行：

```powershell
docker compose -f infra/docker-compose.worker.yml up --build -d
docker compose -f infra/docker-compose.worker.yml ps
docker compose -f infra/docker-compose.worker.yml logs --tail 80 worker
```

首次构建会下载 Python 和锁定的依赖。镜像构建明确排除了 `.env`、上传资料和本地
虚拟环境；`.env` 只在运行时注入。Worker 不开放网络端口。

API 默认存储在 `backend/data/materials`；该目录挂到 Worker 的 `/app/data/materials`。
**两端必须使用同一目录、同一个数据库和同一个 Redis 库号**，否则会出现“上传成功，
后台找不到文件／作业”。运行不同 Git 分支时尤其要核对这一点。

Worker 默认通过 `host.docker.internal` 连接本机 5432／6379 端口，不会重新创建已有
数据库容器。修改过连接或存储路径时，在 PowerShell 设置
`CAMPUSFLOW_WORKER_DATABASE_URL`、`CAMPUSFLOW_WORKER_REDIS_URL`、
`CAMPUSFLOW_HOST_STORAGE_DIR`，或使用 `docker compose --env-file backend/.env ...`。
不要输出 `docker compose config` 的完整结果，因为其中可能含运行时密钥。

Linux／WSL 内直接运行时，从 `backend/` 执行：

```bash
uv run --locked python -m campusflow.workers.main
```

Windows 原生 Python 不支持当前 RQ Worker 的 Linux 进程方式，应使用上述 Docker 命令。
`--burst` 表示处理完当时的队列就退出；日常常驻运行不要加这个参数。

## 3. 三种输入的检查步骤

打开 `http://127.0.0.1:8000/docs`，先调用 `POST /api/v1/auth/login` 登录。
开发默认账号见 `.env.example`；浏览器收到的会话 Cookie 用于后续接口。

| 输入 | 操作 | 应检查的结果 |
| --- | --- | --- |
| 粘贴文本 | `POST /api/v1/materials/text`，填写 `title` 和 `content` | 返回 201，`material` 与 `chunks`；段落号从 1 开始 |
| 文字 PDF | `POST /api/v1/materials/upload`，在 `files` 选择文件 | 返回 201；逐个检查 `results` 的 `error`／`duplicate_of`／`job_id`，不能只看 HTTP 状态 |
| 图片或扫描 PDF | 同一上传接口 | Worker 调用视觉模型；扫描页回退结果保留真实页码，失败范围可见 |

上传成功后，用 `GET /api/v1/jobs/{job_id}` 查询进展：

- `pending`：等待投递或领取；`enqueued_at` 为空时尚未成功入队。
- `running`：后台正在处理。
- `done`：本阶段完成，不代表后续 AI 事项提取或问答已完成。
- `partial`：部分成功；查看 `scope`，不要忽略未识别页或区域。
- `failed`：失败；查看 `error_reason` 和 `retryable`。

终态 `is_terminal=true` 后可停止轮询。`GET /jobs/{id}/attempts` 可查看尝试历史。
接口均加 `/api/v1` 前缀。用 `GET /materials/{id}/versions` 查询版本，再用
`GET /materials/{id}/versions/{version_no}/chunks` 核对文字、页码、段落或坐标，
`GET /materials/{id}/versions/{version_no}/file` 读取对应原文件。

模型返回的文字和区域仍需人工核对；不得把这一步成功当作识别准确性已验收。
重复上传会返回已有资料提示，不会为相同文件再创建作业。

## 4. 只读诊断

在 `backend/` 执行：

```powershell
uv run --locked python -m campusflow.workers.diagnostics
uv run --locked python -m campusflow.workers.diagnostics --job-id 123
```

或检查 Worker 容器看到的环境：

```powershell
docker compose -f infra/docker-compose.worker.yml run --rm worker uv run --locked --no-dev python -m campusflow.workers.diagnostics
```

输出为 JSON：数据库是否可达及结构是否最新、Redis 是否可达及队列长度、存储目录是否
可读写、视觉密钥是否填写；指定作业时只输出编号、阶段、状态、次数、重试性及投递标记。
不会输出口令、文件路径、资料标题／正文或原始异常，也不会重投、回收、删除任何作业。
退出码 0 表示数据库／Redis／存储检查通过，1 表示至少一项失败。
`ready=true` **不代表 Worker 存活或模型调用成功**，还需结合 Compose 状态及作业结果。

## 5. 查错与安全重试

| 现象 | 检查与处理 |
| --- | --- |
| 数据库结构不是最新 | 确认选对数据库，执行 `alembic upgrade head`；不要手动改 `alembic_version` |
| `pending` 且没有投递时间 | 先恢复 Redis，再调用 `POST /api/v1/jobs/redeliver`；只补投当前空间的待处理作业 |
| 已有投递时间但一直 `pending` | 检查 Worker 是否退出、密钥是否填写、两端 Redis 库号是否相同 |
| Worker 报来源不可用 | 核对两端挂载目录；资料已删除则不应重试或恢复旧结果 |
| `partial`／`failed` | 只有返回 `retryable=true` 时才调用 `POST /api/v1/jobs/{id}/retry`；随后重新查状态 |
| 长时间 `running` | 先停掉卡住的 Worker，再重启 API 执行超时回收，确认状态变为可重试失败后重试 |
| 401／404 | 重新登录，核对当前空间和编号；跨空间与不存在都返回 404，不通过改库绕过授权 |

补投成功计数表示进入队列，**不表示解析成功**。资料和待投递记录同时入库，Redis
中断不会撤销已保存资料。默认 API 启动会补投并回收超时作业，这不是周期定时任务。
`CAMPUSFLOW_DISPATCH_ON_STARTUP=false` 只关闭启动补投；超时回收仍在 API 启动时执行。
不要让多个旧、新 Worker 同时处理同一测试数据，不要删除 Redis／数据库卷来“修复”问题。

错误日志只保留作业编号、阶段和受控错误分类；RQ 失败记录不保存原始堆栈。需要更多
信息时查看授权状态接口和脱敏后的尝试记录，不打开包含资料正文的调试日志。

## 6. 无模型费用的演示与验证范围

`--demo` 使用假识别器，只允许名称以 `_test` 结尾的专用测试数据库。其固定输出
“假识别结果”仅用于验证管道，**不能作为真实识别效果或课程事实**。演示应使用独立
Redis 库号和临时存储目录，避免消费日常队列。正式 Worker 不加 `--demo`。

后端回归检查：

```powershell
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
```

完成记录需区分：单元／接口测试、真实 Redis + Linux Worker 测试、真实模型评测、
D019 页面手工操作。前三种输入的后台验证不能代替最后一项。

## 7. 停止与更新

```powershell
docker compose -f infra/docker-compose.worker.yml stop worker
# 更新代码后重建镜像再启动
docker compose -f infra/docker-compose.worker.yml up --build -d
```

不要用 `down -v` 清除数据库卷。备份与删除规则见 [数据保留说明](data-retention.md)。
