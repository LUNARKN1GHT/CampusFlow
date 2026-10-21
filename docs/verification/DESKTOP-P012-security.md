# P012 桌面安全加固验收

对应 Issue：#478（P012：跨平台验收、文档与发布）。实现分支：`issue/#478`，基线由
`integration/desktop-p001-p005` 合入（P001／P004／P005）。本文只记录本分支实际实现
并有测试证据的后端控制项；Electron 外壳相关项由 P008 负责，本分支没有 Electron
代码，全部标记为未验证。

## 交付内容

- 新增 `backend/src/campusflow/api/desktop_security.py`：
  - `install_desktop_security(app, settings)`：仅 `deployment_mode == "desktop"` 时
    安装中间件；服务端模式不注册任何中间件。
  - Host 允许列表：`127.0.0.1`、`localhost`、`::1`，可带端口；支持 `[::1]`、
    `[::1]:8000` 与大小写不敏感的主机名。缺失、重复、带用户信息、端口非法或
    外部域名一律返回 HTTP 400 JSON，不进入路由。
  - 写入方法（POST／PUT／PATCH／DELETE）的 Origin 允许列表：带 `Origin` 时必须是
    `http://127.0.0.1[:port]`、`http://localhost[:port]` 或 `http://[::1][:port]`，
    否则返回 HTTP 403 JSON；无 `Origin` 的请求（非浏览器客户端）放行。
  - 所有桌面模式响应追加 `X-Content-Type-Options: nosniff`、
    `Referrer-Policy: no-referrer`、`X-Frame-Options: DENY`。
  - 仅 `text/html` 响应追加 CSP；`/docs`、`/redoc`、`/openapi.json`（Swagger／ReDoc
    依赖 CDN 资源）与 `/api/v1/...` JSON 不附加 CSP。
  - 纯 ASGI 中间件：只在 `http.response.start` 上补头部，不读取请求体，不缓冲上传。
  - `X-Forwarded-Host`／`X-Forwarded-For` 不参与任何判断，不能用来绕过 Host 校验。
- `backend/src/campusflow/main.py`：在 `install_static_spa` 之前调用
  `install_desktop_security(app, settings)`。
- `backend/tests/test_desktop_security.py`：新增 72 个参数化／独立用例。
- `backend/tests/test_desktop_mode.py`：桌面模式客户端改用
  `base_url="http://127.0.0.1"`，让 Host 校验真实生效；服务端模式用例保持默认
  `testserver`，证明服务端行为未变。
- 前端兼容改动：`index.html` 原有的首屏主题内联脚本外置为
  `frontend/public/theme-boot.js`，并同步更新 `useTheme.ts` 注释。严格 CSP
  `script-src 'self'` 不允许内联脚本，若不外置，深色主题首屏预置会被浏览器拦截。
  逻辑与原内联脚本一致，仍从同源加载并在样式表之前执行。

## 中间件顺序

`create_app` 中先注册业务路由，再调用 `install_desktop_security`，最后调用
`install_static_spa`。Starlette 的 ASGI 栈为：

```
ServerErrorMiddleware
  └─ DesktopSecurityMiddleware（仅桌面模式）
       └─ ExceptionMiddleware
            └─ Router（API／文档）
                 └─ SPA 404 回退处理器（static_spa）
```

因此 Host／Origin 校验在路由匹配前执行；SPA 首页、history 回退、静态资源与 API
响应都会经过同一个 `send` 包装器补安全头。

## CSP 内容

```
default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';
img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none';
base-uri 'none'; frame-ancestors 'none'
```

`style-src 'unsafe-inline'` 用于兼容 Vue 的内联样式；`script-src 'self'` 不允许内联
脚本，因此首屏主题脚本已外置为 `/theme-boot.js`（见上节），与 Vite 构建产物的外链
module script 一起由同源加载。

## 验证证据

命令（后端工作目录 `backend/`）：

```
uv run pytest tests/test_desktop_security.py -q
uv run pytest tests/test_desktop_mode.py -q
uv run pytest -q
uv run ruff check .
```

结果：

- `tests/test_desktop_security.py`：**72 passed**（参数化展开后的用例数）。
- `tests/test_desktop_mode.py`：**12 passed**。
- 后端全量：**1657 passed, 6 skipped, 4 warnings in 255.65s**（`CAMPUSFLOW_TEST_DATABASE_URL`
  使用默认 `campusflow_test`）。同机其他 worktree 的并发全量测试曾在运行中造成表锁等待与
  `remaining connection slots` 连接错误；在独立 `campusflow_integration_test` 上同样得到
  `1656 passed, 6 skipped`，可确认首次失败属于环境争用而非本分支用例失败。
- `uv run ruff check .`：`All checks passed!`。
- `cd frontend && npm ci && npm run build`：类型检查与构建通过
  （`✓ 88 modules transformed`，`✓ built in 2.04s`）；产物包含 `theme-boot.js`，
  `dist/index.html` 已无内联脚本。

### 手工 TestClient 演示

桌面模式、`base_url="http://127.0.0.1"`、临时 SPA 目录：

```
external Host -> 400 {'detail': '桌面模式仅接受本机回环地址访问，请通过 127.0.0.1 或 localhost 打开。'}
  proof headers: {'content-type': 'application/json; charset=utf-8',
                  'x-content-type-options': 'nosniff',
                  'referrer-policy': 'no-referrer',
                  'x-frame-options': 'DENY'}
cross-origin POST -> 403 {'detail': '桌面模式拒绝跨站请求：请求来源必须与本机页面同源。'}
SPA / -> 200 text/html; charset=utf-8
  content-security-policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'
  x-content-type-options: nosniff
  referrer-policy: no-referrer
  x-frame-options: DENY
GET /api/v1/health -> 200 {'status': 'ok', 'service': 'campusflow-api'}
  content-security-policy: None
  x-content-type-options: nosniff
loopback-origin POST -> 200 {'username': 'student'}
```

### 用例与覆盖对照

| 控制项 | 覆盖用例 | 结论 |
| --- | --- | --- |
| 服务端模式不拦截 Host／Origin、不追加头 | `test_server_mode_ignores_host_and_origin_and_adds_no_headers` | 通过 |
| 允许的 Host 变体（含端口、大小写、IPv6 方括号） | `test_desktop_allows_loopback_hosts`、`test_allowed_host_variants` | 通过 |
| 外部／畸形 Host → 400 JSON | `test_desktop_rejects_external_and_malformed_hosts`、`test_malformed_or_external_hosts_are_rejected_by_parser` | 通过 |
| 缺失 Host → 400 | `test_desktop_rejects_request_without_host`（直接 ASGI 调用） | 通过 |
| 重复 Host → 400 | `test_desktop_rejects_duplicate_host_headers`（直接 ASGI 调用） | 通过 |
| 跨站写入 → 403 | `test_desktop_rejects_cross_origin_writes`、`test_desktop_rejects_cross_origin_for_all_write_methods` | 通过 |
| 回环 Origin 与无 Origin 写入到达处理器 | `test_desktop_allows_loopback_origin_writes`、`test_desktop_allows_write_without_origin` | 通过（登录 200／凭据错误 401） |
| `X-Forwarded-Host` 不能绕过 | `test_desktop_x_forwarded_host_cannot_bypass` | 通过 |
| SPA HTML 带 CSP 与基础安全头 | `test_spa_html_has_csp_and_security_headers` | 通过 |
| API JSON 有基础头、无 CSP | `test_api_json_has_security_headers_but_no_csp` | 通过 |
| `/docs` 不被 CSP 破坏 | `test_docs_keeps_working_without_csp` | 通过（200，无 CSP） |
| 静态资源、404、HEAD 与 history 回退 | `test_static_assets_and_404_keep_headers`、`test_history_fallback_and_login_flow_work_under_security` | 通过（含 `/theme-boot.js` 同源加载） |

## Electron 侧期望（P008 负责，本分支未验证）

本分支没有 Electron 主进程或 `webPreferences` 代码，以下均**未实现、未验证**：

- 只加载 sidecar 的 `http://127.0.0.1:<port>`，不使用 `file://` 加载构建产物。
- `BrowserWindow` 使用 `contextIsolation: true`、`nodeIntegration: false`、
  `sandbox: true`，preload 只暴露最小 API。
- 通过 `will-navigate` 与 `setWindowOpenHandler` 阻止导航到外部源，拒绝新窗口；
  外部链接交给系统浏览器且需用户确认。
- 不调用 `webSecurity: false`，不关闭同源策略；不通过 `session.webRequest`
  移除本中间件写入的 CSP／安全头。
- 渲染进程不需要额外 `<meta http-equiv="Content-Security-Policy">`；若添加，
  不得弱于服务端头。

## 剩余风险与未验证项

- **仅回环不等于身份认证**：同一台机器上的其他用户或进程可以访问
  `127.0.0.1:<port>`。Host 校验只阻止浏览器 DNS 重绑定，不阻止本机进程直接请求。
- **单用户内存会话**：会话令牌存在进程内，重启全部失效；没有持久会话、账号体系
  或多用户隔离。这是既有边界，本任务未改变。
- **HTTP 而非 TLS**：桌面 sidecar 走明文 HTTP；本机高权限进程仍可嗅探。没有证书
  固定、mTLS 或 HTTPS。
- **Cookie `Secure=False`**：因本机 HTTP 开发所需，既有实现；不声称可防本机窃听。
- **无独立 CSRF 令牌**：依赖 `Origin` 校验与 `SameSite=Lax`；无 Origin 的非浏览器
  请求按设计放行，若本机进程可自由发请求，则 CSRF 防护不覆盖该威胁。
- **模型密钥在环境变量中**：`ZHIPU_API_KEY`／`DEEPSEEK_API_KEY` 由进程环境读取，
  同用户进程可读取；未接入系统钥匙串。
- **日志脱敏未审计**：本任务未新增日志，也未验证所有既有日志不含密钥／原文；
  标为未验证。
- **未处理异常响应**：Starlette `ServerErrorMiddleware` 位于本中间件外层，未捕获的
  500 响应不会经过安全头包装；未写用例验证，标为低风险遗留。
- **CSP 只验证响应头存在**：本任务未用真实浏览器验证 CSP 实际拦截内联脚本／外部
  资源；前端构建兼容性通过 `npm run build` 证明，但运行时行为未测。
- **真实 Electron／浏览器同源流程**：未在本分支启动打包后的桌面应用；P004 的
  TestClient 与浏览器验证不覆盖 Host／Origin 加固。
- **反向代理场景**：桌面模式拒绝非回环 Host 且忽略 `X-Forwarded-*`；若用户把
  sidecar 放在代理后，请求会被 400 拒绝，这是有意行为，未提供代理模式。
- **WebSocket**：当前应用没有 WebSocket 路由；中间件对非 HTTP scope 直接放行，
  未来新增 WebSocket 时需重新评估来源校验。

## 风险清单

| 等级 | 项目 | 状态 | 依据 |
| --- | --- | --- | --- |
| 高 | DNS 重绑定：仅接受回环 Host | 已实现并测试 | `test_desktop_rejects_external_and_malformed_hosts`、`test_desktop_x_forwarded_host_cannot_bypass` |
| 高 | 跨站写入（CSRF）：写入方法 Origin 允许列表 | 已实现并测试 | `test_desktop_rejects_cross_origin_for_all_write_methods` |
| 高 | 浏览器注入面：`nosniff`、`no-referrer`、`DENY`、HTML CSP | 已实现并测试（仅头部） | `test_spa_html_has_csp_and_security_headers`、`test_api_json_has_security_headers_but_no_csp` |
| 中 | Electron `contextIsolation`／沙箱／导航拦截 | 未实现（P008） | 本分支无 Electron 代码 |
| 中 | 会话安全（内存会话、HTTP、`Secure=False`） | 既有边界，未改变 | 代码现状；本任务未新增会话机制 |
| 中 | 模型密钥与日志脱敏 | 未验证 | 未审计、无测试 |
| 中 | 本机其他进程可访问回环端口 | 接受风险 | 仅靠操作系统用户隔离 |
| 低 | 未处理 500 不经过安全头 | 遗留风险 | `ServerErrorMiddleware` 在中间件外层 |
| 低 | `/docs`、`/redoc` 无 CSP | 有意豁免 | Swagger 依赖 CDN |
| 低 | 无 Origin 写入放行 | 有意设计 | 非浏览器客户端兼容 |

## 结论

桌面模式下，Host 允许列表、写入请求 Origin 校验、基础安全响应头与 HTML CSP 已由
后端测试覆盖；服务端模式行为保持不变；前端构建未受影响。Electron 外壳控制、真实
浏览器 CSP 拦截、打包运行时的端到端验证不在本任务范围内，不得据本文宣称已完成。
