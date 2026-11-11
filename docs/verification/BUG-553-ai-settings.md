# #553 桌面 AI 服务配置入口：实现与验证

- Issue：#553（桌面版缺少可操作的 AI 配置入口，问答与图片识别受阻）
- 分支：`issue/#553`
- 基线：`8b82122`（main 合并 `#550` 后）
- 日期：2026-11-11

## 1. 问题与范围

模型密钥此前只从环境变量读取（`ZHIPU_API_KEY`、`DEEPSEEK_API_KEY`、
`CAMPUSFLOW_TEXT_PROVIDER`，默认 `zhipu`），设置页只有时区、容量与可用时间；
但资料处理与问答的错误提示却让用户"在设置中填写模型密钥"。桌面安装包没有
环境变量入口，用户既无法配置也无法从提示里找到入口。

本轮交付应用内配置入口，范围限定为**模型密钥与文字供应方**：不改变模型调用、
检索、索引与问答协议，不新增模型供应商，不自动重启后台进程。

## 2. 交付物

| 层 | 文件 | 内容 |
| --- | --- | --- |
| 应用（启动编排） | `backend/src/campusflow/application/ai_settings.py` | 密钥读写、供应方偏好文件、启动加载、状态汇总、保存校验 |
| API | `backend/src/campusflow/api/ai_settings.py` | `GET/PUT /api/v1/ai-settings` |
| 装配 | `backend/src/campusflow/main.py` | 注册路由；`create_app` 启动时加载已保存配置 |
| 装配 | `backend/src/campusflow/workers/desktop.py` | 桌面 Worker 在 `configure_worker` 之前加载已保存配置 |
| 装配 | `backend/src/campusflow/sidecar/cli.py` | `load_settings()` 加载已保存配置（serve／worker／doctor 等共用） |
| 提示 | `backend/src/campusflow/workers/model_hints.py` | 缺失密钥时按部署模式给出可操作入口 |
| 提示 | `backend/src/campusflow/workers/assembly.py`、`embeddings.py`、`generation.py` | 视觉、向量、文字适配器缺密钥文案指向实际入口 |
| 前端 | `frontend/src/views/SettingsView.vue` | 「AI 服务」面板：状态、供应方、密码输入、清除与保存 |
| 前端 | `frontend/src/api/settings.ts`、`frontend/src/api/types.ts` | `aiSettingsApi` 与 `AiSettingsStatus` 类型 |
| 测试 | `backend/tests/test_ai_settings.py` | 15 个用例，见第 8 节 |

## 3. 接口契约

`GET /api/v1/ai-settings`（需要登录会话）：

```json
{
  "editable": true,
  "text_provider": "zhipu",
  "zhipu_configured": true,
  "deepseek_configured": false,
  "text_available": true,
  "vision_available": true,
  "embedding_available": true,
  "secret_backend": "macos-keychain",
  "restart_required": false,
  "warnings": []
}
```

- 不返回密钥明文、片段、长度或指纹；只返回布尔值与后端名称。
- `editable` 只在 `CAMPUSFLOW_DEPLOYMENT_MODE=desktop` 时为 `true`。
- 非桌面模式的 `secret_backend` 固定为 `environment`，说明配置来自环境变量；
  未检测到任何密钥时给出一条警告。
- `text_available` 取决于当前供应方对应的密钥；`vision_available` 与
  `embedding_available` 只取决于 ZHIPU 密钥（图片识别与向量检索都走智谱适配器）。
- `restart_required` 默认 `false`；同一进程内成功保存后变为 `true`，
  提示后台 Worker 需要重启，直到进程重启。

`PUT /api/v1/ai-settings`（仅桌面模式，服务器模式返回 403）：

```json
{
  "text_provider": "deepseek",
  "zhipu_api_key": "（可选，提交即覆盖）",
  "deepseek_api_key": "（可选，提交即覆盖）",
  "clear_zhipu_api_key": false,
  "clear_deepseek_api_key": false
}
```

- 全部字段可选；用 pydantic `model_fields_set` 区分「未提交」与「提交空值」，
  未提交的字段保持原值。
- 提交空字符串或纯空白：400，提示"密钥不能为空；如需删除已保存的密钥请勾选清除"。
- 同一密钥同时填写与清除：400。
- 保存后最终文字供应方必须有对应密钥（`zhipu` → ZHIPU 密钥，`deepseek` →
  DeepSeek 密钥），否则 400 且**不写入任何一项**（先校验、后落库）。
- 未知字段：422（`extra="forbid"`），避免拼错字段名后"看起来保存成功"。
- 成功响应与 GET 同结构，`restart_required=true`，同样不含密钥。
- 保存成功后同步更新 `request.app.state.settings`：API 进程内的文字生成、
  向量与问答适配器按请求从 `app.state.settings` 构造，无需重启即可使用新配置。

## 4. 存储与安全

- 密钥复用 P007 的 `SecretStore`（`infrastructure/secrets.create_secret_store`），
  根目录为 `settings.data_dir`，账户名 `ai-zhipu-api-key`、`ai-deepseek-api-key`；
  后端优先沿用 `state.json` 记录的 `secret_backend`（与数据库口令同一存储），
  再回退到 `CAMPUSFLOW_SECRET_BACKEND`。
- 文字供应方偏好不是密钥，写入 `<data_dir>/ai-settings.json`（临时文件 +
  `os.replace` 原子写入，权限 0600），内容只有 `{"text_provider": "..."}`。
  文件缺失视为未设置；JSON 损坏、顶层不是对象或取值非法时回退默认 `zhipu`，
  并在状态 `warnings` 中给出可见提示，不让启动失败。
- 密钥不进入日志、诊断输出、异常文本或接口响应；领域错误只说明缺什么、
  怎么补，不回显取值。sidecar 的统一脱敏使用加载后的设置，存储密钥同样被替换。
- 桌面模式下 `data_dir` 必填且为绝对路径（既有 `Settings` 校验），否则启动即失败。
- 桌面模式之外本模块是 no-op：不读数据目录、不创建凭据存储、不写偏好文件。

## 5. 启动加载与环境变量优先

`apply_stored_ai_settings(settings)` 在三处调用：`main.create_app`、
`workers/desktop.py` 的 `main()`（在 `configure_worker` 之前）、
`sidecar/cli.py` 的 `load_settings()`。规则：

- 构造 `Settings` 时已显式给出的字段（环境变量、`.env` 或调用方传参）优先，
  存储值不会覆盖；未给出的字段才用已保存值补齐。快照取自赋值之前的
  `model_fields_set`（pydantic 的赋值同样会写入该集合）。
- 数据目录不存在（首启之前）、凭据存储不可用或读取失败：只记录固定警告，
  绝不阻塞 API／Worker 启动，也不回显异常原文。
- 分层：`core/config.py` 不依赖基础设施；密钥与偏好读写留在应用启动编排层，
  与 `application/bootstrap.py` 同层，`main`／`workers`／`sidecar` 只负责调用。

## 6. 错误指引文案

| 位置 | 之前 | 现在（桌面模式） | 现在（服务器模式） |
| --- | --- | --- | --- |
| 视觉解析 | 请在设置中填写模型密钥后重试 | 请在设置 → AI 服务填写 ZHIPU API 密钥后重启应用并重试 | 请在环境变量 ZHIPU_API_KEY 配置后重启服务并重试 |
| 向量索引 | 未配置向量模型密钥，无法建立索引；文本解析结果已保留 | 同上入口，提示"重新建立索引" | 同上，说明环境变量 |
| 文字生成 | 缺少 ZHIPU_API_KEY，不能调用真实文字模型 | 追加"请在设置 → AI 服务填写 …" | 追加"请在环境变量 … 配置" |

文案只包含变量名与入口名称，不含密钥。

## 7. 前端

设置页新增「AI 服务」面板（沿用现有 `panel`／`availability-grid`／
`status-pill`／`stack-form` 与语义色 token，未新增色值）：

- 三张状态卡：文字生成（显示当前供应方）、图片识别、向量检索；显示"可用"或
  "缺少密钥"。
- 显示安全存储后端（macOS 钥匙串／Windows DPAPI／0600 凭据文件／环境变量），
  并说明页面只显示是否已保存。
- 桌面可编辑时：供应方下拉、ZHIPU／DeepSeek 两个 `type=password` 输入
  （留空表示不修改，浏览器不自动填充）、两个"清除已保存密钥"勾选与保存按钮。
  前端只提交用户实际填写的字段，留空密钥不会发送。
- 保存成功后显示"AI 配置已保存，文字问答与检索立即使用新密钥；后台资料处理
  需重启应用生效。"，并在 `restart_required` 为真时持续显示重启提示。
- 非桌面模式只读：说明由 `ZHIPU_API_KEY`、`DEEPSEEK_API_KEY`、
  `CAMPUSFLOW_TEXT_PROVIDER` 配置。
- 面板独立加载：AI 配置读取失败只影响本面板，不阻塞时区与可用时间编辑。

## 8. 验证

```bash
cd backend
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest tests/test_ai_settings.py -q
uv run --locked pytest -q

cd ../frontend
npm ci
npm run build   # 含 vue-tsc 类型检查
```

结果：

- `uv run ruff check .`：All checks passed。
- `uv run --locked pytest tests/test_ai_settings.py -q`：15 passed。
- 合并到集成分支后复跑 `tests/test_ai_settings.py tests/test_jobs_api.py tests/test_material_recognition.py tests/test_material_status_views.py tests/test_desktop_worker.py`：66 passed。
- `npm run build`（含 `vue-tsc --noEmit`）：通过。
- 设置页 AI 服务面板在本机浏览器以服务器模式加载，显示只读环境变量配置状态；桌面可编辑路径由后端测试覆盖，未在打包安装包内实测。

## 9. 未覆盖项与已知限制

- **未使用真实模型密钥做在线验证**：只验证配置保存、状态与装配路径，没有用
  真实 ZHIPU／DeepSeek 密钥发起模型调用（与 Q001/Q019 条款一致，真实校务资料
  不得发往外部接口）。
- **未在打包后的 Electron 应用内手工验证**：前端只做到类型检查与构建通过，
  没有启动安装包逐项点击；"重启应用生效"依赖 Electron 重启进程的实际行为，
  需要安装包验收时复核。
- **后台 Worker 不会自动重启**：保存后仅 API 进程立即生效；解析与索引作业在
  Worker 重启前仍按旧配置运行（无密钥时以明确原因失败，不伪造结果）。
- **422 回显边界**：密钥以非字符串结构提交时，FastAPI 的通用校验响应会回显
  该字段原文；正常字符串密钥不会出现在任何响应中，本机单用户场景不构成实际
  泄露路径，记录在此以便后续统一错误响应时收敛。
- **环境变量提供的密钥不能在设置页清除**：清除后最终供应方没有密钥会被拒绝；
  环境变量的优先级也意味着重启后仍会生效。桌面安装包不提供这类环境变量。
- **未新增数据库表与迁移**：配置只在本机凭据存储与数据目录文件里。
