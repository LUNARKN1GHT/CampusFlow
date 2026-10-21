# macOS 桌面端到端联调记录（未签名）

日期：2026-10-21。范围：本地集成分支 `integration/desktop-p001-p005` 上的打包应用，使用随包 PyInstaller sidecar 与随包 PostgreSQL 17.11 + pgvector。本文只记录实际执行过的内容；签名、公证、Windows 与干净机器仍未验证。

## 构建

```text
cd frontend && npm run build
bash desktop/build_macos.sh                     # PyInstaller one-folder，91 MB，46.4 s
desktop/vendor/pgsql/bin/postgres --version     # PostgreSQL 17.11（沿用 P003a 已验证的暂存目录）
cd desktop/electron && npm ci && npm run build
npx electron-builder --mac --arm64 --publish never
```

产物：`desktop/electron/release/CampusFlow-0.1.0-mac-arm64.dmg`，未签名（本机无 Developer ID 证书）。应用包内资源：

```text
Resources/sidecar/campusflow-sidecar        98 MB
Resources/pgsql/                            22 MB（PostgreSQL 17.11 + vector.dylib）
Resources/static/                          436 KB（SPA）
Resources/THIRD-PARTY-NOTICES.md
```

## 启动配置与阶段

使用临时数据目录启动打包后的可执行文件（避免污染用户目录）：

```text
CAMPUSFLOW_DATA_DIR=/tmp/cf-e2e-data
CAMPUSFLOW_LOG_DIR=/tmp/cf-e2e-logs
CAMPUSFLOW_API_PORT=8125
CAMPUSFLOW_PG_PORT=55433
CAMPUSFLOW_PG_BIN=<repo>/desktop/vendor/pgsql/bin
CAMPUSFLOW_SIDECAR_BIN=<repo>/desktop/dist/campusflow-sidecar/campusflow-sidecar
```

实际日志：

```text
阶段开始：postgres → 阶段完成：postgres
阶段开始：migrate  → 阶段完成：migrate
阶段开始：api      → API 健康检查通过：http://127.0.0.1:8125/api/v1/health
阶段开始：worker   → 后台 Worker 已启动
状态：phase=ready stage=ready 全部服务已就绪，正在加载界面…
启动完成：所有阶段就绪，端口 8125
```

- `GET /api/v1/health` → 200；`GET /` → 200（同源 SPA）。
- Playwright 登录后 `GET /api/v1/courses` → 200 `[]`；`/plan` history 路由正常渲染。
- 退出（SIGTERM）时按 Worker → API → PostgreSQL 逆序停止，日志显示“子进程已回收，应用退出”；复查无残留 sidecar、postgres 或 Electron 进程。

## 联调中发现并修复的问题

1. `initdb` 只创建角色与模板库，业务库不存在导致迁移失败 → 启动后创建业务库再迁移。
2. Electron 子进程未设置 `CAMPUSFLOW_DEPLOYMENT_MODE=desktop`，Worker 误走 RQ 并要求 Redis → 子进程环境显式设为 desktop。
3. 桌面模式缺少模型密钥时 Worker 直接退出 → 桌面模式允许启动：文本解析继续，图片识别与向量索引在真正调用时以“未配置模型密钥”永久失败，不伪造结果。
4. 打包后未自动发现 `Resources/sidecar`、`Resources/pgsql`、`Resources/static` → 运行时按 `process.resourcesPath` 探测，源码运行保持原有回退。
5. PyInstaller 入口与 P006 CLI 两套命令 → 统一委托 `campusflow.sidecar.cli:main`。

## 首启初始化与自动登录（P008 接入）

2026-10-21 在同一集成分支上接入 P007 bootstrap，并用重新打包的 `--dir` 应用复验。默认流程改为 `bootstrap → API → Worker`：Electron 不再执行 `initdb --auth=trust`、`createdb` 或 `migrate`，数据库口令与本机登录口令都由 sidecar 生成并保存到凭据后端。

### 开关

| 变量 | 作用 |
| --- | --- |
| `CAMPUSFLOW_BOOTSTRAP=1` | 强制启用首启初始化（源码运行也可用） |
| `CAMPUSFLOW_BOOTSTRAP=0` | 关闭 bootstrap，回到旧 supervisor 的 trust + migrate 流程；仅用于测试／诊断，不用于发布 |
| 未设置 + 打包应用 | 默认启用；`CAMPUSFLOW_MANAGED_POSTGRES=external` 时关闭 |
| `CAMPUSFLOW_ELECTRON_STUB=1` | 测试桩模式永远跳过 bootstrap |
| `CAMPUSFLOW_AUTO_LOGIN=0` | 关闭主窗口自动登录，保留手动登录页 |

### 构建与启动

```text
cd desktop/electron && npm run build
npx electron-builder --mac dir --arm64 --publish never

CAMPUSFLOW_BOOTSTRAP=1
CAMPUSFLOW_DATA_DIR=/tmp/cf-p008-e2e-data
CAMPUSFLOW_LOG_DIR=/tmp/cf-p008-e2e-logs
CAMPUSFLOW_API_PORT=8126
CAMPUSFLOW_PG_PORT=55435
CAMPUSFLOW_PG_BIN=<repo>/desktop/vendor/pgsql/bin
CAMPUSFLOW_SIDECAR_BIN=<repo>/desktop/dist/campusflow-sidecar/campusflow-sidecar
CAMPUSFLOW_SECRET_BACKEND=file          # 自动化验证避免钥匙串交互；默认 auto 使用 macOS 钥匙串
```

bootstrap 会自行选择空闲回环端口并把实际端口写入 `state.json` 与连接串；`CAMPUSFLOW_PG_PORT` 仍会传入，但 bootstrap 模式不强制使用该端口。

实测阶段日志（`/tmp/cf-p008-e2e-logs/main.log`）：

```text
启动顺序：bootstrap → API → Worker（首启初始化由 sidecar 负责）
自动登录：启用
阶段开始：bootstrap
首启初始化完成：迁移头 k001_external_calendar，凭据后端 file，PostgreSQL 由 bootstrap 管理
阶段完成：bootstrap
阶段开始：api → 阶段完成：api
阶段开始：worker → 阶段完成：worker
状态：phase=ready stage=ready 全部服务已就绪，正在加载界面…
自动登录成功：campusflow
```

- `GET /api/v1/health` → 200；`GET /` → 200。
- 主窗口最终 URL 为 `http://127.0.0.1:8126/`（离开 `/login`），`GET /api/v1/auth/session` → 200 `{"username":"campusflow"}`，无自动登录失败提示。
- 同一数据目录第二次启动可重复执行 bootstrap（集群已初始化时只做就绪检查），自动登录同样成功。
- 退出后按 Worker → API → `pg_ctl -D <data_dir>/pgdata -m fast -w stop` 停止 bootstrap 启动的集群；复查无 `CampusFlow`、`campusflow-sidecar` 或该数据目录下的 `postgres` 残留进程。

### 数据与凭据检查

```text
<data_dir>/{pgdata,materials,logs,backups,run,state.json,secrets.json}
state.json: username=campusflow, pg_version=17.11, port=62391（随机端口，每次启动可能变化）,
            schema_head=k001_external_calendar, secret_backend=file
secrets.json: 0600，含 db-password 与 local-password
```

- `secrets.json` 中的本机口令不等于 `campusflow-dev`，数据库口令不等于 `campusflow`。
- `main.log`、`stage-api.log`、`stage-worker.log` 中 `student`／`campusflow-dev` 出现次数为 0；两个随机口令都不出现在日志中。
- bootstrap 失败时错误页显示阶段「首启初始化」、`bootstrap 退出码 N`、最近 stderr 与诊断日志目录（`<data_dir>/logs`），且不启动 API（Playwright 用例覆盖）。

### 测试

`cd desktop/electron && npm run typecheck && npm test`：40 passed。新增覆盖：

- supervisor：`bootstrap → migrate（升级闸门）→ API → Worker → ready` 顺序、不再调用 `initdb`／`createdb`／`migrate`、API／Worker 环境含连接串与本机凭据、退出调用 `pg_ctl stop`、bootstrap 失败与 `ready=false` 停在 bootstrap 阶段、数据库比安装包新时拒绝启动、维护暂停/恢复。
- 数据维护：导出取消与成功摘要、导入检查/确认/令牌/失败恢复/非法输出、升级已最新/拒绝降级/备份升级/失败恢复。
- 配置：打包／`CAMPUSFLOW_BOOTSTRAP`／`CAMPUSFLOW_MANAGED_POSTGRES`／测试桩的启用规则，`CAMPUSFLOW_AUTO_LOGIN=0`。
- 自动登录：登录页填充与提交、未跳转时 1 秒后重试一次、失败只提示用户名且异常信息不含口令、失败提示条脚本不含口令。
- Electron：测试桩模式不出现 bootstrap 阶段；假 sidecar 退出 1 时显示错误页且不启动 API；应用菜单包含「数据」导出／导入／升级入口。

### 数据维护与升级闸门（2026-10-21 复跑）

同一打包应用重新构建后，用全新数据目录复跑：

```text
阶段开始：bootstrap → 阶段完成：bootstrap   （迁移头 k001_external_calendar，凭据后端 macos-keychain）
阶段开始：migrate   → 数据库结构已是最新，无需升级（约 0.3 s） → 阶段完成：migrate
阶段开始：api       → 阶段完成：api
阶段开始：worker    → 阶段完成：worker
状态：phase=ready stage=ready …  自动登录成功：campusflow
```

- 本次 bootstrap 默认使用 macOS 钥匙串（未设置 `CAMPUSFLOW_SECRET_BACKEND=file`）。
- `GET /api/v1/health` → 200；`GET /` → 200；SIGTERM 后按 Worker → API → PostgreSQL 逆序停止，无残留进程。
- 未通过菜单实际执行导出／导入对话框（会阻塞在原生保存/确认窗口）；菜单存在与流程分支由 Playwright 用例覆盖，归档本身的真实读写见 `DESKTOP-P010-transfer.md` 的 CLI round-trip。

## 未验证 / 已知缺口

- **签名、公证、Gatekeeper**：本产物未签名，不能作为正式发布包。
- **首启凭据**：默认流程已改为 P007 bootstrap（随机口令、凭据后端、自动登录），不再使用 `student/campusflow-dev`；`CAMPUSFLOW_BOOTSTRAP=0` 仍保留旧 trust 流程，只用于测试和排障。
- **macOS 钥匙串**：本轮自动化显式使用 `CAMPUSFLOW_SECRET_BACKEND=file`；打包应用默认 `auto` 走钥匙串的路径沿用 P007 源码验证，未在打包应用中复跑。
- **自动登录失败提示**：真实运行中未制造失败，只由单元测试覆盖；页面内提示条的实际样式未截图。
- **`.campusflow` 与升级命令**：菜单「数据」已接入导出／导入／升级检查，导入前暂停 API 与 Worker、成功后重启；打包应用内通过原生对话框实际执行的流程未自动化，真实跨机器迁移仍未验证。
- **Windows**：未构建、未运行。
- **干净机器**：本机安装有 Python、Node、Homebrew PostgreSQL；未在真正空白的 macOS 上验证。
- **真实模型**：未配置模型密钥，未调用真实识别或向量服务。
- **DMG 安装流程**：未执行拖入 Applications 后的首次启动；本轮直接运行 `.app` 内可执行文件。
