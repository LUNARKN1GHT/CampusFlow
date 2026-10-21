# P002a PyInstaller sidecar 验证：macOS arm64

作者：李亦凡；任务 [#481](https://github.com/LUNARKN1GHT/CampusFlow/issues/481)；父任务 [#468](https://github.com/LUNARKN1GHT/CampusFlow/issues/468)。日期：2026-10-21。

范围：证明 FastAPI 后端可以作为 PyInstaller one-folder sidecar 在 macOS arm64 上启动，迁移脚本与原生依赖随包可用；不包含内置 PostgreSQL、Electron/Tauri 外壳、签名、公证与安装器。

## 结论摘要

- 在 macOS 27.0.1（Apple M5，arm64）、Python 3.12.13、PyInstaller 6.22.3 下构建成功，产物 **90 MB**，构建耗时 **33.5 秒**。
- 从 `/tmp` 下复制出的产物、使用 `env -i` 剥离环境变量后，`serve` 能启动并返回 `/api/v1/health` 200、`/ready` 200。
- 冻结进程用随包 `_internal/migrations` 对空库执行 **49 个迁移**，到达 `k001_external_calendar`，建出 45 张表；迁移目录与仓库 `backend/migrations` 逐文件一致。
- 冻结 Worker 通过本机 Redis 完成 4 个作业（2 个解析、2 个索引），其中包含 DOCX 解析和“文本页 + 扫描图片页” PDF 的 pypdfium2 渲染与视觉回退。
- 原生依赖随包：`libpython3.12.dylib`、`libpq.5.dylib`、`libpdfium.dylib`、`cryptography` 的 Rust 扩展均为 arm64，可执行文件为 PyInstaller 自动 ad-hoc 签名。
- **未在真正没有 Python/uv 的干净机器或全新用户/虚拟机上验证**；本次用“产物复制到仓库外 + `env -i` + PATH 不含 Homebrew/uv”近似。Gatekeeper/quarantine、公证、Intel x64、Windows、Electron 外壳未验证。

## 环境与版本

| 项目 | 实测值 |
| --- | --- |
| macOS | 27.0.1（Build 26A434） |
| 芯片/架构 | Apple M5 / arm64 |
| Python | 3.12.13（`uv venv --python 3.12`） |
| uv | 0.11.6 |
| PyInstaller | 6.22.3（bootloader 与 hooks 随 pip 包） |
| PostgreSQL | 16.14（Homebrew，本机 127.0.0.1:5432） |
| Redis | 8.10.2（仅 Worker 验收时本机启动，端口 6380） |
| Docker | 未使用 |

## 交付物

- `desktop/sidecar_entry.py`：`serve`、`migrate`、`worker` 三个子命令。`serve` 固定绑定 `127.0.0.1`，端口可配置；`migrate` 用绝对 `script_location` 执行随包迁移；`worker` 复用现有 RQ Worker 装配，支持 `--burst`、`--demo`。
- `desktop/campusflow-sidecar.spec`：one-folder 规格；显式 datas 为 `backend/migrations`、`backend/alembic.ini`、`python-docx` 模板、`pypdfium2_raw` 库；不使用 `--onefile`。
- `desktop/build_macos.sh`：创建独立 `desktop/.build-venv`，`uv pip install -e ./backend pyinstaller` 后构建到 `desktop/dist/`。
- `.gitignore`：忽略 `desktop/.build-venv/`、`desktop/build/`、`desktop/dist/`。

未修改后端应用代码。迁移定位逻辑在 `desktop/sidecar_entry.py` 内单独实现，候选顺序与 P004 的 `core/resources.py` 一致（显式参数 → `sys._MEIPASS` → 源码 `backend/migrations`），但本分支不依赖 P004 才能构建。

## 构建

```text
$ ./desktop/build_macos.sh
Python 3.12.13 (arm64)
6.22.3
...
== 构建耗时：33.5 秒 ==
 90M	desktop/dist/campusflow-sidecar
产物：desktop/dist/campusflow-sidecar/campusflow-sidecar
```

产物顶层只有两项：

```text
desktop/dist/campusflow-sidecar/
├── campusflow-sidecar        # Mach-O 64-bit executable arm64
└── _internal/                # 74 MB：Python 运行时、依赖与 datas
```

关键资源：

```text
_internal/alembic.ini
_internal/migrations/             # 49 个版本文件 + env.py + script.py.mako
_internal/docx/                   # 含 templates/default.docx
_internal/pypdfium2_raw/libpdfium.dylib
_internal/libpq.5.dylib
_internal/libpython3.12.dylib
_internal/psycopg_binary/_psycopg.cpython-312-darwin.so
_internal/cryptography/hazmat/bindings/_rust.abi3.so
```

`codesign -dv` 显示可执行文件为 `flags=0x2(adhoc)`、`Signature=adhoc`，由 PyInstaller 构建过程自动签名，无需手工 `codesign --deep`。

## 干净环境启动

把 `dist/campusflow-sidecar` 复制到 `/tmp/campusflow-p002a/campusflow-sidecar`，工作目录设为 `/tmp/campusflow-p002a/run`（**没有仓库源码**），并用 `env -i` 只保留 HOME、PATH 和必要配置：

```text
$ cd /tmp/campusflow-p002a/run
$ env -i HOME=/tmp/campusflow-p002a/home PATH=/usr/bin:/bin:/usr/sbin:/sbin \
    CAMPUSFLOW_DATABASE_URL=postgresql+psycopg://campusflow:campusflow@127.0.0.1:5432/campusflow_p002a_test \
    CAMPUSFLOW_REDIS_URL=redis://127.0.0.1:6380/0 \
    CAMPUSFLOW_STORAGE_DIR=/tmp/campusflow-p002a/data \
    /tmp/campusflow-p002a/campusflow-sidecar/campusflow-sidecar serve --port 18080
INFO:     Started server process [25816]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:18080 (Press CTRL+C to quit)
```

进一步把 PATH 收窄为 `/bin:/usr/sbin:/sbin`（不含 `/usr/bin`）再启动一次，`/api/v1/health` 与 `/ready` 仍为 200，说明进程不依赖外部 Python、uv 或仓库文件。

限制：这台机器本身仍装有 Homebrew Python 与 uv；本次通过清空环境变量、移除 PATH 中的 Homebrew/uv、复制产物到仓库外来近似“干净机器”，**不等于**在全新用户或虚拟机验证。

## 冷启动与健康检查

从 `Popen` 到 `/api/v1/health` 首次返回 200 的墙钟时间（外部测量脚本，不在产物内）：

| 轮次 | 冷启动 |
| --- | --- |
| 首次启动（产物刚复制到 /tmp，系统缓存较冷） | **3748 ms** |
| 第二次启动 | 546 ms |
| 第三次启动 | 528 ms |
| 带 `CAMPUSFLOW_CALENDAR_TOKEN_KEY` 启动（初始化 Fernet） | 740 ms |

首次明显更慢，主要是可执行文件与 dylib 首次载入；后续稳定在约 0.5–0.7 秒。

健康检查输出：

```text
GET /api/v1/health -> 200
{"status":"ok","service":"campusflow-api"}

GET /api/v1/ready -> 200
{"status":"ok","service":"campusflow-api","database":"ok"}
```

把数据库地址指向不可达端口时，两个探针按设计分离：

```text
GET /api/v1/health -> 200 {"status":"ok","service":"campusflow-api"}
GET /api/v1/ready  -> 503 {"detail":"数据库不可用"}
```

## 迁移

新建空库 `campusflow_p002a_test`（owner `campusflow`）。由于 `q004` 迁移包含 `CREATE EXTENSION vector`，而 `campusflow` 不是超级用户，先用本机超级用户预装 `vector` 扩展；这是本机测试库权限准备，不是打包缺陷。首次未预装时迁移在 q004 处明确报 `permission denied to create extension "vector"`，其余流程正常。

```text
$ cd /tmp/campusflow-p002a/run
$ env -i HOME=... PATH=/usr/bin:/bin:/usr/sbin:/sbin \
    CAMPUSFLOW_DATABASE_URL=postgresql+psycopg://campusflow:campusflow@127.0.0.1:5432/campusflow_p002a_test \
    /tmp/campusflow-p002a/campusflow-sidecar/campusflow-sidecar migrate

迁移目录：/private/tmp/campusflow-p002a/campusflow-sidecar/_internal/migrations
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> c95d3606e7ee, initial: ...
...
INFO  [alembic.runtime.migration] Running upgrade c007_merge_c003 -> k001_external_calendar, ...
```

结果：

```text
Running upgrade 行数：49
alembic_version：k001_external_calendar
public schema 表数：45
```

`migrate --revision current` 同样从冻结资源读取版本历史。`diff -rq backend/migrations dist/.../_internal/migrations` 与 `alembic.ini` 对比均无差异。

## Worker

本机启动 Redis 8.10.2（`127.0.0.1:6380`，未使用 Docker），通过 API 上传 DOCX 与混合 PDF（第 1 页文本、第 2 页纯图片），再用冻结产物消费队列：

```text
$ env -i HOME=... PATH=/usr/bin:/bin:/usr/sbin:/sbin \
    CAMPUSFLOW_DATABASE_URL=...campusflow_p002a_test \
    CAMPUSFLOW_REDIS_URL=redis://127.0.0.1:6380/0 \
    CAMPUSFLOW_STORAGE_DIR=/tmp/campusflow-p002a/data \
    /tmp/campusflow-p002a/campusflow-sidecar/campusflow-sidecar worker --burst --demo

Worker ...: campusflow-parse: parse-1
Successfully completed job parse-1 in 0:00:00.190209s
...
Worker ...: campusflow-index: index-2
Successfully completed job index-2 in 0:00:00.205828s
...
Worker ...: campusflow-parse: parse-3
Successfully completed job parse-3 in 0:00:00.184016s
...
Worker ...: campusflow-index: index-4
Successfully completed job index-4 in 0:00:00.202001s
```

`--demo` 只允许数据库名以 `_test` 结尾，使用假视觉与假向量，不调用真实模型。数据库结果：

```text
processing_jobs: 1 parse done / 2 embed done / 3 parse done / 4 embed done
materials: 1 word done / 2 pdf done
source_chunks: DOCX 2 条；PDF 第 1 页文本 + 第 2 页 region“假识别结果”
indexed_blocks: 4
```

PDF 第 2 页的 `假识别结果` 来自“pypdfium2 渲染扫描页 → 视觉解析器”的回退路径，证明随包 `libpdfium.dylib` 在冻结进程内可加载并运行。DOCX 两条片段证明 `python-docx` 默认模板已随包。

边界：当前 `worker` 子命令复用服务端 **RQ + Redis** 路径。P005 的数据库轮询 Worker 在 `issue/#471`，依赖 `db_queue`、`queue/factory`、`worker/assembly` 与仓储改动，无法只摘一个模块；本分支未合入 P005，因此“桌面部署不需要 Redis 的 Worker”仍待 P005/P006。

## 原生依赖

```text
$ file desktop/dist/campusflow-sidecar/campusflow-sidecar
Mach-O 64-bit executable arm64

$ otool -L .../campusflow-sidecar
    /usr/lib/libSystem.B.dylib
    /usr/lib/libz.1.dylib

$ otool -L .../_internal/libpq.5.dylib
    @rpath/libpq.5.dylib
    @rpath/libssl.3.dylib
    @rpath/libcrypto.3.dylib

$ otool -L .../_internal/pypdfium2_raw/libpdfium.dylib
    @rpath/libpdfium.dylib
    /System/Library/Frameworks/AppKit.framework/...
    /System/Library/Frameworks/CoreGraphics.framework/...
    /System/Library/Frameworks/CoreFoundation.framework/...

$ otool -L .../_internal/psycopg_binary/_psycopg.cpython-312-darwin.so
    @rpath/libpq.5.dylib
    /usr/lib/libSystem.B.dylib
```

`libpython3.12.dylib`、`cryptography` 的 `_rust.abi3.so` 也都是 arm64；后者由运行时 hook 自动收集。

## 隐藏导入与包数据

显式加入的 hidden imports（spec 中）：

- `campusflow.core.config`、`campusflow.infrastructure.db.base`、`campusflow.infrastructure.db.models`：Alembic 以 `exec` 方式运行 `migrations/env.py`，静态分析看不到这些导入。
- `sqlalchemy.dialects.postgresql.psycopg`：SQLAlchemy 按 URL 动态选择方言。
- `psycopg_binary._psycopg`、`psycopg_binary._uuid`、`psycopg_binary.pq`：`psycopg[binary]` 运行时选择 C 扩展。
- `multipart`、`python_multipart`：FastAPI 表单/文件路由。
- `pypdfium2`、`pypdfium2_raw`、`pypdfium2_raw.bindings`、`pypdfium2_raw.version`：ctypes 绑定与 dylib。
- `h11`；`uvicorn`、`rq`、`alembic` 的全部子模块（协议、执行器、命令等动态入口）。

包数据：

- `backend/migrations` → `_internal/migrations`（含 49 个版本、`env.py`、`script.py.mako`）。
- `backend/alembic.ini` → `_internal/alembic.ini`。
- `collect_data_files("docx")` → `_internal/docx`（默认模板）。
- `collect_data_files("pypdfium2_raw")` + `collect_dynamic_libs("pypdfium2_raw")` → `_internal/pypdfium2_raw/libpdfium.dylib`。

构建告警中出现的 `gunicorn`、`websockets`、`wsproto`、`watchfiles`、`greenlet`、`psycopg2`、`mysql` 等均为未安装的可选依赖或未使用方言，不影响本次启动、迁移与 Worker。未发现必须补充的隐藏导入：首次构建即通过全部验收路径。

失败与注意项：

- 迁移依赖数据库超级用户预装 pgvector；sidecar 只负责执行迁移，不负责安装扩展。
- `worker` 非 `--demo` 模式仍需 `ZHIPU_API_KEY`，本次未验证真实模型调用。
- 首次在 q004 处失败后重跑会从已提交的 revision 继续；测试时以空库重跑得到完整 49 条记录。

## 未验证

- **Gatekeeper/quarantine 与公证**：产物未从浏览器下载、未打 quarantine 标记，未执行 `spctl`/公证；ad-hoc 签名只保证本机可执行。
- **全新机器/全新用户/虚拟机**：未在真正没有 Python、uv 的 macOS 上验证；本次为同机剥离环境近似（见上）。
- **Intel x64、Windows、Linux**：未构建或运行。
- **Electron/Tauri 外壳、内置 PostgreSQL、安装器、自动更新**：不在本任务范围。
- **P005/P006 数据库轮询 Worker**：未合入，桌面无 Redis 场景待后续任务。
- **真实 ZHIPU 视觉/文字/向量模型在冻结进程中的调用**：只用 `--demo` 假适配器验证作业链路。
- **长期运行、端口冲突、崩溃重启、日志轮转**：未做压力与稳定性测试。

## 回归检查

本机同时有其他 worktree 在跑全量测试，为避免共用 `campusflow_test` 互相清表，使用独立测试库 `campusflow_p002a_pytest_test`（名称仍以 `_test` 结尾，满足测试保护），因此耗时包含并行机器的数据库争用：

```text
$ CAMPUSFLOW_TEST_DATABASE_URL=postgresql+psycopg://campusflow:campusflow@127.0.0.1:5432/campusflow_p002a_pytest_test \
    uv run pytest
=========== 1563 passed, 6 skipped, 4 warnings in 345.06s (0:05:45) ============
```

```text
$ uv run ruff check .
All checks passed!
$ uv run ruff check ../desktop/sidecar_entry.py
All checks passed!
```

## 复现命令

```bash
# 构建（依赖 uv；产物在 desktop/dist）
./desktop/build_macos.sh

# 迁移（需先把 CAMPUSFLOW_DATABASE_URL 指向可迁移库）
desktop/dist/campusflow-sidecar/campusflow-sidecar migrate
desktop/dist/campusflow-sidecar/campusflow-sidecar migrate --revision current

# 启动 API
CAMPUSFLOW_DATABASE_URL=... desktop/dist/campusflow-sidecar/campusflow-sidecar serve --port 8000

# Worker（需要 Redis；--demo 仅限 *_test 库）
CAMPUSFLOW_DATABASE_URL=... CAMPUSFLOW_REDIS_URL=... \
  desktop/dist/campusflow-sidecar/campusflow-sidecar worker --burst --demo
```


## 集成更新（P006 合入后）

P002a 最初在 `desktop/sidecar_entry.py` 内维护独立的 serve／migrate／worker。P006 的统一 CLI 合入后，入口改为只转发 `campusflow.sidecar.cli:main`，spec 增加 `campusflow.sidecar`、`campusflow.workers` 与 `campusflow.infrastructure.queue` 的 `collect_submodules`。本地重建 91 MB one-folder（46.4 s）；在 `/tmp` 以 `env -i` 执行 `--help`、`doctor --json`、`serve --port 8999`，`/api/v1/health` 返回 200。原先的 RQ Worker 验证保留，桌面数据库 Worker 由 `worker --mode auto` 在 desktop 模式下选择。
