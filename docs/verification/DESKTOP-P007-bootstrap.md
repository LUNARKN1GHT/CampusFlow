# P007 桌面首启初始化与凭据验收

对应 Issue：#473。实现分支：`issue/#473`。基线：`integration/desktop-p001-p005`。

## 交付

- `application/bootstrap.py`：首启用例、状态文件、恢复行为与生成连接环境变量。
- `application/ports/secrets.py`：`SecretStore` 端口与 `Secret` 掩码包装。
- `infrastructure/secrets/`：macOS 钥匙串（`security`）、Windows DPAPI（ctypes）、0600 文件兜底与内存实现。
- `infrastructure/db/postgres_cluster.py`：`initdb`／`pg_ctl`／`pg_isready` 封装、随机回环端口、数据目录内套接字。
- `infrastructure/db/provisioning.py`：建库、启用 pgvector、读取迁移头（psycopg，不经 shell）。
- `infrastructure/db/migrations.py`：新增 `build_runtime_alembic_config` 与 `upgrade_to_head`。
- `core/config.py`：新增 `CAMPUSFLOW_PG_BIN`、`CAMPUSFLOW_SECRET_BACKEND`。
- `backend/tests/test_bootstrap.py`：真实 initdb／启动／迁移的集成用例。

## 提供给 sidecar 的接口（P006）

```python
inspect(settings: Settings, *, secret_store: SecretStore | None = None) -> BootstrapState
initialize(settings: Settings, *, secret_store: SecretStore | None = None) -> BootstrapReport
ensure_ready(settings: Settings, *, secret_store: SecretStore | None = None) -> BootstrapReport
connection_env(report: BootstrapReport) -> dict[str, str]
```

`secret_store` 只用于测试与显式注入；正式调用传两个位置参数即可。报告字段：`status`、`paths`、`ready`、`initialized`、`started_postgres`、`postgres_running`、`connection`、`local_username`、`local_password`、`schema_head`、`secret_backend`、`warnings`、`messages`。`connection.database_url()` 是唯一取连接串的入口，`connection_env` 返回 `CAMPUSFLOW_DATABASE_URL` 与 `CAMPUSFLOW_LOCAL_PASSWORD`，不写文件。

## 契约

- `initialize` 在临时同级目录（`<data_dir>.init-<random>`）完成 initdb、启动、建库、`CREATE EXTENSION vector`、`alembic upgrade head`，随后停止集群并原子改名为 `<data_dir>`；返回报告时集群是停止的，调用方接着调用 `ensure_ready`。原子改名要求先停止服务器，否则数据目录路径与运行中服务器不一致。
- 失败时停止集群并把日志留在 `<data_dir>.failed-<timestamp>/logs`，不生成带 `state.json` 的数据目录；已写入的凭据一并删除。
- `state.json` 只含 `format_version`、`created_at`、`pg_version`、`port`、`database`、`username`、`schema_head`、`secret_backend`；格式版本高于应用时判为 `incompatible` 并拒绝覆盖。
- `ensure_ready` 在已初始化但集群停止时启动并校验；本地登录口令缺失时重新生成并写回凭据存储，数据库口令缺失时返回 `partially_initialized` 并明确提示“不会使用默认口令”，不静默回退 `student/campusflow-dev`。
- 桌面模式 gate：非 `desktop` 或相对数据目录直接 `BootstrapError`。

## 凭据后端

| 平台 | 后端 | 行为 |
| --- | --- | --- |
| macOS | `macos-keychain` | `security add-generic-password`（口令经 stdin，不进 argv），服务名 `campusflow-desktop` |
| Windows | `windows-dpapi` | `CryptProtectData` 密文文件 `secrets.dpapi`（未实机验证） |
| 其它 | `file` | `<data_dir>/secrets.json`，0600，报告与日志给出降级警告 |
| 任意 | `auto` 下密钥链失败 | 回退 0600 文件并记录警告；显式指定 `keychain`／`dpapi` 失败则中止 |

## 验证证据

- `uv run pytest tests/test_bootstrap.py`：`13 passed, 2 skipped`（钥匙串往返默认跳过，Windows DPAPI 在非 Windows 跳过）。
- 后端全量（隔离测试库 `campusflow_p007_test`）：`1598 passed, 8 skipped`（`EXIT=0`）。此前两次运行各出现 1 个与本改动无关的失败／错误（`remaining connection slots are reserved for roles with the SUPERUSER attribute`，同时有其它工作树的全量测试在跑），单独复跑对应用例均通过。隔离库需先由超级用户执行 `CREATE EXTENSION vector`。
- `uv run ruff check .`：`All checks passed!`。
- 手工 cycle（临时目录，本机 PostgreSQL 16.14，`CAMPUSFLOW_SECRET_BACKEND=file`）：

```
[1] inspect before init: uninitialized
[2] initialize: initialized initialized= True ready= False postgres_running= False
[3] state.json:
{
  "created_at": "2026-10-21T06:55:16Z",
  "database": "campusflow",
  "format_version": 1,
  "pg_version": "16.14",
  "port": 59945,
  "schema_head": "k001_external_calendar",
  "secret_backend": "file",
  "username": "campusflow"
}
[4] secrets in state.json? False False
[4] secrets.json mode: 0o600
[5] passwords != campusflow-dev: True | local_username: campusflow
[6] ensure_ready: initialized ready= True started= True port= 59945
[7] connection_env keys: ['CAMPUSFLOW_DATABASE_URL', 'CAMPUSFLOW_LOCAL_PASSWORD']
[8] pg_isready: 127.0.0.1:59945 - 接受连接
[9] psql extension/schema/port: 0.8.7 / k001_external_calendar / 59945 / 45 张表
[10] socket dir mode: 0o700 socket: ['.s.PGSQL.59945', '.s.PGSQL.59945.lock']
[11] after external stop, port open? False
[12] restart via ensure_ready: started= True ready= True
[13] second initialize (idempotent): False state unchanged: True
[14] leftover postgres pids: []
```

- 手工 keychain 后端（同一临时目录，`CAMPUSFLOW_SECRET_BACKEND=auto`）：`secret_backend: macos-keychain`、未生成 `secrets.json`、`security` 读回口令与报告一致、`ensure_ready` 通过；结束后删除两条钥匙串项（`find-generic-password` 返回 44）。
- `uv sync --locked`：通过（无依赖变更）。

## 未验证

- Windows DPAPI 已在 windows-2022 CI 实跑：`desktop-windows-build.yml` 在内置 PostgreSQL
  就绪后运行 `tests/test_bootstrap.py`，13 passed、3 skipped（跳过的为 POSIX 专用与
  macOS 钥匙串用例），覆盖 DPAPI 往返与真实 initdb 首启。个人电脑与安装后的应用未验证；
  `0600` 在 Windows 上只影响只读位。
- macOS 钥匙串在无图形会话／CI 环境下的可用性未验证；自动化用例默认跳过真实钥匙串写入（`CAMPUSFLOW_TEST_KEYCHAIN=1` 才执行）。
- Electron 自动登录与钥匙串读取由 P008 接入，本轮只提供跨平台凭据存储抽象与 `connection_env`。
- 打包内置的 PostgreSQL 17 未参与验证；本机验证使用 Homebrew PostgreSQL 16.14 与 pgvector 0.8.7，代码不假设版本号。
- 端口在“探测空闲”与“PostgreSQL 绑定”之间仍存在理论竞争窗口，未实现自动换端口重试。
- 数据目录路径过长时套接字回退到系统临时目录（回环 TCP 不受影响），该分支仅在路径长度检查层面覆盖，未构造超长路径实跑。
