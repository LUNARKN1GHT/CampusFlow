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

## 未验证 / 已知缺口

- **签名、公证、Gatekeeper**：本产物未签名，不能作为正式发布包。
- **首启凭据**：当前 supervisor 仍是 `initdb --auth=trust` + 默认 `student/campusflow-dev`；P007 的随机口令与钥匙串 bootstrap 已实现但**尚未接入 Electron 启动链**。这违反 #466“不出现默认口令”的验收，需在 P007 收尾时接入。
- **`.campusflow` 与升级命令**：CLI 已提供 export/import/upgrade，但 Electron 菜单/界面入口尚未接入。
- **Windows**：未构建、未运行。
- **干净机器**：本机安装有 Python、Node、Homebrew PostgreSQL；未在真正空白的 macOS 上验证。
- **真实模型**：未配置模型密钥，未调用真实识别或向量服务。
- **DMG 安装流程**：未执行拖入 Applications 后的首次启动；本轮直接运行 `.app` 内可执行文件。
