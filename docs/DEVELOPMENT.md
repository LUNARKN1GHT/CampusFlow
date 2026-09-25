# 本地开发

当前可运行内容为 Vue 前端环境页、Python API 健康检查和 OpenAPI 文档，不需要数据库、Docker、Redis 或模型密钥。技术方案和目标架构见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 环境

- Python 3.12，由 `backend/.python-version` 指定；uv 可在同步时寻找或安装相应解释器。
- [uv](https://docs.astral.sh/uv/getting-started/installation/)。
- Node.js 建议 24，最低 22.12，使用 npm；版本提示见 `frontend/.nvmrc`。
- 首次安装依赖需要访问 Python 和 npm 包仓库。

以下命令从仓库根目录打开终端执行，前后端分别占用一个终端。不需要手动激活 Python 虚拟环境。

## 启动后端

```bash
cd backend
uv sync --locked
uv run --locked uvicorn campusflow.main:app --reload --host 127.0.0.1 --port 8000
```

默认配置即可启动。需要自定义时复制 `backend/.env.example` 为同目录 `.env` 并修改；目前只提供 `CAMPUSFLOW_APP_NAME`。环境变量优先于 `.env`，不要把 `.env` 提交到仓库。

- 健康检查：<http://127.0.0.1:8000/api/v1/health>
- Swagger UI：<http://127.0.0.1:8000/docs>
- OpenAPI：<http://127.0.0.1:8000/openapi.json>

健康检查成功响应为 `{"status":"ok","service":"campusflow-api"}`，仅验证 API 进程存活。业务接口尚未实现，访问不存在的地址应返回 404。

## 启动前端

在另一个终端从仓库根目录运行：

```bash
cd frontend
npm ci
npm run dev
```

访问 <http://127.0.0.1:5173>。页面通过 Vite 代理调用真实后端，能显示检查中、连接成功、连接失败，并支持重试。5173 被占用时会直接报错，不自动切换端口。

前端请求统一使用相对 `/api` 地址。如调整后端端口，同步修改 `frontend/vite.config.ts` 的代理目标；不要把模型密钥写入前端环境变量。

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
```

`build` 已包含类型检查，提交前执行一次 `npm run build` 即可。界面修改还应在浏览器中检查显示和交互；后续业务页面加入时再增加对应组件和端到端测试。

仓库根目录可运行 `git diff --check` 检查空白问题。依赖变更必须同步锁文件：后端使用 `uv add`／`uv lock`，前端使用 `npm install`；日常安装使用 `uv sync --locked` 和 `npm ci`。

## 当前限制

- 暂无数据库迁移、Worker 命令、Docker Compose、认证、上传、RAG 和排期实现；各预留目录的 README 只说明职责。
- `npm run preview` 只预览构建后的静态页面，未配置生产 API 反向代理；完整联调使用 `npm run dev`。
- 开发服务仅绑定本机地址，当前不用于公网部署。后续共享部署需要认证、访问范围控制与反向代理配置。
- Swagger UI 使用外部静态资源；离线时 UI 可能无法加载，可直接访问 `/openapi.json` 查看接口描述。
- 许可证仍待小组决定。
