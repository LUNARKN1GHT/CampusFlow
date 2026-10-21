# F005 评估：Windows 与 macOS 桌面分发与打包方案

- Issue：[#381（F005）](https://github.com/LUNARKN1GHT/CampusFlow/issues/381) · 父 Issue：[#380（EF1）](https://github.com/LUNARKN1GHT/CampusFlow/issues/380)
- 范围：后续探索，未承诺开发阶段或日期；本结论不启动实现
- 日期：2026-10-19

## 结论摘要

CampusFlow 现在不是“双击一个文件就能用”的桌面程序，而是“浏览器 + Python 后端 + PostgreSQL + Redis + Worker”的组合。把它打包成 Windows/macOS 程序，难点不在外壳，而在数据库、队列、后台 Worker、跨平台签名和升级维护。

**近期建议选路线 A：Docker 一键本机包**。它完全复用现有技术栈，数据库、Redis、Worker 都不用重写，工作量最小，也与 Issue #400 已记录的倾向一致。Electron/Tauri 原生安装包暂不立项；只有当真实用户量和跨平台验证证明“必须像普通 App 一样安装”时，再评估 Tauri + Python 子进程方案。

建议阶段：**后续探索/课程演示，不纳入 MVP**。前置条件：至少一台 Windows 和一台 macOS 实机、Docker Desktop 可用、数据备份与重置流程确定。未通过范围决策前不开始实现。

## 名词先用大白话解释

| 名词 | 大白话 |
| --- | --- |
| Docker / 容器 | 把软件和它需要的运行环境装进一个标准“集装箱”，换电脑也能按同样方式启动。Docker Desktop 是 Windows/macOS 上运行这些集装箱的桌面程序。 |
| Docker Compose | 一份说明书，告诉 Docker 要同时启动哪几个集装箱（数据库、缓存、后端、网页），以及它们怎么连起来。 |
| Electron | 把一个自带浏览器内核的桌面程序，用来包住现有网页；装出来的程序体积较大，但各系统显示效果一致。 |
| Tauri | 类似 Electron，但不自带浏览器，借用系统已有的浏览器内核；安装包小，但不同系统的浏览器内核有差异。 |
| WebView | 系统自带的“网页显示组件”。Tauri 依赖它，Electron 自己带一份。 |
| sidecar / 子进程 | 桌面外壳启动时，顺手在后台拉起 Python 后端或 Worker。用户看不到终端窗口，但程序要负责它们的启动、退出和崩溃重启。 |
| 代码签名 | 给安装包办一张“身份证”，告诉 Windows/macOS“这个软件是谁做的、有没有被改过”。没有身份证，系统会弹警告。 |
| 公证（notarization） | 把 macOS 安装包交给苹果自动检查一遍并盖章；没有盖章，较新的 macOS 会阻止直接打开。 |
| 自动更新 | 程序自己检查新版本、下载并替换旧版本。需要更新服务器、签名密钥和回滚方案。 |

## 当前基线

- 前端：Vue 3 + TypeScript + Vite，开发时通过 Vite 把 `/api` 转发到 `127.0.0.1:8000`。
- 后端：Python 3.12 + FastAPI，依赖 PostgreSQL 17 + pgvector、Redis 7、RQ Worker。
- 开发方式：`infra/docker-compose.yml` 只启动 db/redis；API 和前端在宿主机用 `uv`、`npm` 启动；Worker 另有 `infra/docker-compose.worker.yml`。
- 目前**没有**前端生产容器、生产 compose、迁移一次性容器和双击启动脚本。
- 当前开发机没有安装 Docker（2026-10-19 核实），端到端验证需要在有 Docker 的 Windows/macOS 机器上补做。

## 四条路线

### 路线 A：Docker 一键本机包（#400 路线）

**做法**：同学安装一次 Docker Desktop，双击启动脚本；脚本检查 Docker、首次构建或拉取镜像、等待数据库就绪、执行迁移，然后启动 API、网页和可选 Worker，最后自动打开浏览器。

**优点**：

- 完全复用现有 Python、PostgreSQL、Redis、RQ Worker，不需要改业务代码。
- 数据库、缓存、文件目录都能用 Docker 命名卷保存，停止/重启不丢数据。
- 开发环境和同学环境基本一致，排查问题容易。
- 不需要给 CampusFlow 自己做 Windows/macOS 安装包签名；Docker Desktop 本身是正规安装的。

**限制**：

- 同学必须先装 Docker Desktop，仍需管理员权限和虚拟化支持（Windows 的 WSL2/Hyper-V，macOS 的虚拟化框架）。
- 首次拉取镜像约 1–2 GB，占磁盘约 2–4 GB；国内网络可能需要镜像加速。
- 不是“原生 App”体验：没有桌面图标、菜单栏集成和系统级自动更新。
- 启动脚本在 Windows/macOS 上仍可能被 SmartScreen/Gatekeeper 提示。

**预估工作量**：前端生产容器 + nginx 同源反代 1–2 天；生产 compose、迁移容器、健康检查 1–2 天；Windows/macOS 双击脚本与中文错误提示 1–2 天；两台实机验证 1–2 天。合计约 **3–5 个工作日**，另加跨平台测试时间。

### 路线 B：Electron 外壳

**做法**：用 Electron 做一个桌面窗口，里面加载现有 Vue 页面；Electron 启动时在后台拉起 Python API 和 Worker。

**优点**：

- 用户看到的是普通安装包，双击安装、桌面图标、自动更新都有成熟方案。
- 前端代码基本不用改。
- 各系统显示效果一致，因为 Electron 自带 Chromium。

**限制**：

- 安装包通常 100–200 MB，内存占用也更高。
- 真正麻烦的是 PostgreSQL、Redis、Worker：要么要求用户另装 Docker（那就退化成“路线 A + 外壳”），要么把数据库和 Redis 二进制作进安装包（升级、备份、杀毒软件误报、Windows 上 Redis 支持有限，维护成本很高），要么把后端改成 SQLite + 进程内队列（业务代码要大改）。
- 需要处理子进程生命周期、端口冲突、日志、崩溃重启、卸载时是否删数据。
- Windows/macOS 都要分别签名、公证和测试。

**预估工作量**：仅外壳 1–2 周；连同数据库/队列/Worker 的真正“下载即用”约 **3–6 周**，且后续维护成本高。

### 路线 C：Tauri 外壳

**做法**：与 Electron 类似，但桌面窗口使用系统 WebView，安装包更小；后台仍要拉起 Python API 和 Worker（Tauri 称为 sidecar）。

**优点**：

- 安装包通常 10–50 MB（不含数据库等附加物），内存和启动更快。
- 比 Electron 更接近“轻量原生 App”。
- 有官方 sidecar 机制，可管理随包携带的 Python 可执行文件。

**限制**：

- Windows 依赖 WebView2，macOS 依赖 WKWebView，显示细节可能有差异；老旧或受管控的 Windows 机器可能缺 WebView2。
- 仍需解决 PostgreSQL、Redis、Worker 的携带问题，难度与 Electron 相同甚至更高。
- 需要 Rust 工具链和跨平台构建经验，团队学习成本更高。
- 同样要签名、公证、自动更新和两台实机测试。

**预估工作量**：仅外壳 2–3 周；连同本地服务约 **4–8 周**。

### 路线 D：单机 Web 服务 + 引导脚本

**做法**：不造桌面外壳。提供脚本检查并安装 Python/uv、Node、Docker，启动数据库、API、Worker 和静态网页，然后打开浏览器。

**优点**：

- 比 Electron/Tauri 少一个桌面外壳，前端构建产物可以直接复用。
- 对会使用终端的同学足够；开发调试方便。

**限制**：

- “下载即用”体验最弱：仍要安装 Python、Node 或 Docker，环境差异大。
- Windows 和 macOS 的包管理器、权限、路径规则不同，脚本分支多。
- 出错时用户很难自己排查。

**预估工作量**：**1–3 周**，但很难达到非技术同学“双击就能用”的目标。

### 对照路线 E：纯公网网页版

**做法**：把现有前后端部署到一台服务器，同学只用浏览器访问，不安装任何东西。

**优点**：用户零安装；更新只维护服务器；跨平台最省心。

**限制**：需要服务器、域名、HTTPS、备份和访问控制；资料隐私和模型费用由部署方承担；不能离线使用。它不是“桌面打包”，但能解决“同学不用装环境”的问题。

## 横向对比

| 维度 | A. Docker 一键包 | B. Electron | C. Tauri | D. 引导脚本 | E. 公网网页版 |
| --- | --- | --- | --- | --- | --- |
| 首次安装 | 装 Docker Desktop + 双击脚本 | 双击安装包 | 双击安装包 | 运行脚本，可能还要装 Python/Node | 打开浏览器 |
| 安装体积 | 约 1–2 GB 镜像，磁盘 2–4 GB | 100–200 MB 起，另加服务 | 10–50 MB 起，另加服务 | 500 MB–1 GB 起 | 0 |
| 是否要 Docker | 是 | 取决于数据库方案 | 取决于数据库方案 | 通常要 | 否 |
| PostgreSQL | 容器 | 另装 Docker / 随包携带 / 改 SQLite | 同左 | 另装 Docker 或本机安装 | 服务器上 |
| Redis / 队列 | 容器 | 随包携带或改队列 | 随包携带或改队列 | 另装 | 服务器上 |
| Worker | 独立容器，可开关 | 子进程，需管生命周期 | sidecar，需管生命周期 | 后台进程/服务 | 服务器上 |
| 离线可用 | 本地功能可用，AI 需联网 | 同左 | 同左 | 同左 | 否 |
| 签名/公证 | 应用本身不需要，脚本可能被提示 | Windows + macOS 都要 | Windows + macOS 都要 | 通常不签名 | 服务器证书 |
| 自动更新 | 不做或手动拉镜像 | 成熟方案，但要自建更新源 | 有方案，但要自建更新源 | 脚本更新 | 服务器统一更新 |
| 预估工作量 | 3–5 个工作日 | 3–6 周 | 4–8 周 | 1–3 周 | 2–5 个工作日部署 |
| 主要风险 | Docker Desktop 门槛、国内拉镜像 | 数据库/队列携带、包体大、跨平台 | Rust 门槛、WebView 差异、数据库携带 | 环境差异、体验差 | 服务器成本、隐私、离线 |

> 体积和工作量为粗略估算，目的是比较量级，不是承诺值；实际以样本构建和实机测试为准。

## PostgreSQL、Redis 与 Worker 怎么处理

### A. Docker 一键包

- PostgreSQL 17 + pgvector、Redis 7 都用容器；数据放命名卷。
- 迁移用一次性 `migrate` 容器，在 API 启动前完成；失败就停止并显示中文原因。
- API 和网页各自一个容器；网页由 nginx 托管静态文件，并在同源反代 `/api`，同时支持 Vue history 路由回退。
- Worker 用 compose profile 控制：配置了 `ZHIPU_API_KEY` 才启动；没有密钥就不启动，页面明确提示 AI 功能不可用。
- 端口只绑定 `127.0.0.1`，不暴露到局域网或公网。
- 备份/重置通过命名卷和文档完成；重置必须二次确认，不能误删。

### B/C. Electron/Tauri

只有三种选择，没有“轻松”的第四种：

1. **要求用户另装 Docker**：数据库和队列最稳，但外壳只解决窗口，没解决安装门槛，等于“路线 A + 外壳”。
2. **随安装包携带 PostgreSQL/Redis 二进制**：真正“下载即用”，但要处理二进制体积、初始化、端口、升级、数据目录、杀毒软件误报和 Windows 上 Redis 支持有限的问题；维护成本最高。
3. **裁剪后端**：把 PostgreSQL 换成 SQLite、把 Redis/RQ 换成进程内队列。业务代码、迁移、并发和任务重试都要改，等于一次后端重构，风险最大。

### D. 引导脚本

依赖用户机器上已有的 Docker 或包管理器；数据库、Redis、Worker 的生命周期由脚本管理。环境差异最大，失败时最难排查。

## 离线可用范围

| 功能 | 无网络时 | 说明 |
| --- | --- | --- |
| 登录、学期/课程/任务/日程管理 | 可用 | 数据和页面都在本机 |
| 上传资料、查看原文 | 可用 | 文件存本机私有目录 |
| DOCX/PDF 文本解析（不涉及 OCR） | 可用 | 本地解析器工作 |
| 核对、字段修正、版本对照 | 可用 | 本地数据库 |
| AI 提取、OCR/视觉识别 | 不可用 | 需要模型服务和密钥 |
| 问答、embedding/向量检索 | 不可用 | 需要模型服务；本地索引可保留，但不能生成新向量 |
| 自动通知更新、外部日历 | 不可用 | 需要联网和授权 |

**桌面打包不等于 AI 离线可用。** 如果课程演示现场没有网络，应提前说明哪些功能不可用，或准备录屏/样例数据。

## 签名、公证、自动更新与安装体验成本

### Windows

- 代码签名证书约 **75–799 美元/年**（OV/EV、供应商不同）；自 2023 年 6 月起，私钥必须放在硬件令牌或云 HSM，不能再用普通 `.pfx` 文件。
- 2026 年起证书有效期进一步缩短到约一年，不能再买多年证书；CI 里通常要用云签名服务。
- 没有签名时，SmartScreen 会提示“未知发布者”；签名后也要靠下载量逐步积累信誉，不是立刻消除警告。
- Docker 路线不需要给 CampusFlow 应用签名，但双击脚本仍可能被提示；可以把脚本一起签名，成本同上。

### macOS

- Apple Developer Program **99 美元/年**；没有付费会员不能做 Developer ID 签名和公证。
- 需要 Developer ID Application 证书、Hardened Runtime、`notarytool` 公证和 stapling；否则 Gatekeeper 会阻止打开。
- 公证是自动检查，不代表苹果审核业务内容；被拒时要按日志修正后重新提交。
- Docker 路线不需要给应用公证，但 `.command` 脚本要可执行，首次打开仍可能被 Gatekeeper 提示。

### 自动更新

- Electron/Tauri：需要自建更新服务器、签名更新包、版本回滚和灰度策略；否则用户只能手动下载新安装包。
- Docker 路线：可以检查新版本并提示“重新拉取镜像”，但不做静默自动更新；课程演示阶段足够。
- 公网网页版：服务器统一更新，用户无感，但需要维护部署流水线。

### 安装体验

| 路线 | 非技术同学的实际步骤 | 主要卡点 |
| --- | --- | --- |
| A. Docker | 装 Docker Desktop → 双击脚本 → 等启动 | Docker 安装、虚拟化、国内拉镜像 |
| B. Electron | 双击安装包 → 打开 | 包体大、首次启动慢、杀毒误报 |
| C. Tauri | 双击安装包 → 打开 | WebView2 缺失、数据库服务仍要解决 |
| D. 脚本 | 打开终端 → 运行脚本 → 按提示装依赖 | 终端、Python/Node/Docker 环境 |
| E. 公网 | 打开网址 → 登录 | 网络、服务器可用性、隐私说明 |

## 建议

1. **近期选 A：Docker 一键本机包**。它和 #400 已记录的路线一致，复用现有 db/redis/worker，工作量最小，最适合课程演示和小范围同学使用。
2. **不建议现在做 Electron/Tauri 原生安装包**。外壳不是难点；把 PostgreSQL、Redis、Python Worker 一起塞进安装包并保证升级、备份、杀毒兼容才是难点。没有真实用户量和双平台验证前，投入产出比低。
3. **建议阶段**：后续探索/课程演示，不纳入 MVP 交付承诺。
4. **是否立项**：F005 本身不立项；如果课程演示需要，按 #400 单独跟踪实现。未来若面向普通消费者，再评估 Tauri + Python sidecar。
5. **前置条件**：
   - 至少一台 Windows 和一台 macOS 实机，已安装 Docker Desktop；
   - 确定数据备份、重置和卸载策略；
   - 首次安装可接受 1–2 GB 下载，或提供离线镜像包；
   - 明确 AI 功能需要联网和密钥，不承诺完全离线；
   - 默认口令只限本机，端口只绑定 `127.0.0.1`。

## 明确不做

- 本项不产出实现代码，不新增 Dockerfile、compose、启动脚本或签名配置。
- 不实现 #400；#400 仍为暂缓状态。
- 不做 Windows/macOS 代码签名、公证或自动更新。
- 不承诺完全离线使用 AI 能力。
- 不改变 README 的分阶段交付承诺。

## 参考

- [Apple Developer Program](https://developer.apple.com/programs/)（会员与 Developer ID）
- [Apple：Choosing a Membership](https://developer.apple.com/support/compare-memberships/)（99 美元/年与公证资格）
- [Docker 定价](https://www.docker.com/pricing/) 与 [Docker Desktop 许可](https://docs.docker.com/subscription/desktop-license/)
- [CA/Browser Forum：Code Signing](https://cabforum.org/working-groups/code-signing/)（代码签名私钥硬件要求）
- [Electron](https://www.electronjs.org/) 与 [electron-builder 公证](https://www.electron.build/notarization/)
- [Tauri](https://tauri.app/) 与 [Tauri Sidecar](https://tauri.app/develop/sidecar/)
- [PyInstaller](https://pyinstaller.org/)（Python 程序打包为可执行文件）
- [嵌入式 PostgreSQL + Tauri + PyInstaller 实践](https://github.com/datadrivenconstruction/OpenConstructionERP)（仅作技术参考，不代表本项目采用）
