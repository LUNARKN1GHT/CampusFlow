# P004 桌面部署模式与静态 SPA 托管验收

对应 Issue：#470。实现分支：`issue/#470`。合并验证分支：`integration/desktop-p001-p005`。

## 交付

- `CAMPUSFLOW_DEPLOYMENT_MODE`（`server`／`desktop`）、`CAMPUSFLOW_DATA_DIR`、`CAMPUSFLOW_STATIC_DIR`、`CAMPUSFLOW_MIGRATIONS_DIR` 配置。
- 桌面模式要求 `CAMPUSFLOW_DATA_DIR` 为绝对路径；未显式覆盖 `storage_dir` 时派生为 `<data_dir>/materials`。
- `api/static_spa.py`：仅桌面模式安装，`/api` 路径不参与回退；真实文件直接返回，缺失的 `assets` 或带扩展名路径保留 404，其余 GET/HEAD 页面请求回退 `index.html`；`/assets` 长缓存、`index.html` 不缓存。
- `core/resources.py` + `infrastructure/db/migrations.py`：迁移目录按显式配置、PyInstaller `sys._MEIPASS`、已安装包数据、仓库 `backend/migrations` 的顺序解析；Alembic 使用绝对 `script_location`。
- `backend/pyproject.toml` 将 `alembic.ini` 与 `migrations` 打入 wheel（`campusflow/*`）。

## 验证证据

- 后端全量（合并后，独立测试库 `campusflow_integration_test`）：`1583 passed, 6 skipped`。
- `backend/tests/test_desktop_mode.py`：12 passed，覆盖绝对路径校验、存储目录派生、首页与 history 路由回退、真实资源缓存、缺失资源 404、未知 API 保持 JSON 404、服务端模式不挂载静态站点、迁移目录解析。
- `uv run ruff check .`：`All checks passed!`。
- `cd frontend && npm run build`：类型检查与构建通过（`✓ built`）。
- 手工 TestClient（使用真实 `frontend/dist` 副本）：

```
GET  /                            -> 200 text/html cache=no-cache
GET  /login?expired=1             -> 200 text/html cache=no-cache
GET  /assets/index-*.js           -> 200 text/javascript cache=public, max-age=31536000, immutable
GET  /assets/missing-abc.js       -> 404 application/json
GET  /api/v1/does-not-exist       -> 404 application/json
GET  /api/v1/health               -> 200 application/json
server GET /                      -> 404 application/json
```

- `uv build --wheel` 产物包含 `campusflow/alembic.ini` 与 `campusflow/migrations/**`。
- 真实浏览器（Playwright Chromium，桌面模式绑定 `127.0.0.1:8123`，静态目录为 `frontend/dist`）：首访重定向 `/login?redirect=/`，登录后进入总览；`/plan` 等 history 路由由 `index.html` 回退并正常渲染；会话 Cookie `campusflow_session` 为 `httpOnly` 且域为 `127.0.0.1`；页面仅有一处登录前的预期 401，无脚本异常。

## 未验证

- 真实 PyInstaller `_MEIPASS` 布局（由 P002a 验证）。
- 打包后桌面运行时的浏览器会话；本轮使用 TestClient 与构建产物副本验证回退规则。
- 绑定内置 PostgreSQL／数据目录的真实首启（P003a、P007）。
