# P003a 内置 PostgreSQL 17 + pgvector（macOS）验证记录

作者：李亦凡；任务 #483；父任务 #469（P003）。分支 `issue/#483`，起点为 `issue/#469` 的整合分支。

- 环境：macOS 27.0.1（arm64），Xcode Command Line Tools，Apple clang 21.0.0（clang-2100.3.34.2）。
- 未修改系统 PostgreSQL 或 Homebrew 管理的任何数据目录；系统 `psql` 为 Homebrew 的 16.14，仅用于确认版本差异，构建与运行都没有调用它。
- 交付物：`desktop/postgres/build_macos.sh`、`desktop/postgres/smoke_macos.sh`、`desktop/postgres/VERSIONS.md`。构建产物在 `desktop/vendor/pgsql`（已 gitignore），仓库只提交脚本、清单和本记录。

## 1. 结论

- 从官方源码构建 PostgreSQL 17.11 与 pgvector 0.8.7，暂存为 **19 MB** 的自包含目录，包含 `postgres`、`initdb`、`pg_ctl`、`pg_isready`、`psql`、`pg_dump`、`pg_restore` 等运行所需工具，不含头文件、静态库、`pg_config`、`ecpg`、PGXS 或 man 文档。
- 产物不依赖 Homebrew：所有 Mach-O 只引用 `/usr/lib` 或 `@loader_path`；44 个 Mach-O 的安装名被改写并重新 ad-hoc 签名。把暂存目录复制到 `/tmp` 并从 `/` 启动后，完整冒烟仍通过。
- 冒烟连续两次通过（随机端口分别 63198、63535），覆盖 `initdb --locale=C --encoding=UTF8 --auth=scram-sha-256`、随机密码、仅 127.0.0.1、`pg_isready`、`CREATE EXTENSION vector`、HNSW 索引、最近邻查询、重启后数据保留、停止后无残留进程。
- 后端全量测试保持通过：`1558 passed, 6 skipped`；`ruff check .` 通过。本任务没有修改后端代码。

## 2. 版本、来源与 SHA-256

| 组件 | 版本 | 来源 | SHA-256 |
| --- | --- | --- | --- |
| PostgreSQL | 17.11 | `https://ftp.postgresql.org/pub/source/v17.11/postgresql-17.11.tar.bz2` | `dd27f2b3c59e73ed14aa3324901242bf69a032a6347805f274e6260322d42979` |
| pgvector | 0.8.7 | `https://github.com/pgvector/pgvector/archive/refs/tags/v0.8.7.tar.gz` | `cac0b10c360f05b2d521200105ba3697e773d4cd3731f5a915a7e37ebe0bea85` |

- PostgreSQL 17.11 是构建时 17.x 的最新源码版本；主版本与 `infra/docker-compose.yml` 的 `pgvector/pgvector:pg17` 一致。
- pgvector 0.8.7 是构建时的最新 release tag，与本机 Homebrew pgvector 版本一致。
- PostgreSQL 归档 SHA-256 与官方 `postgresql-17.11.tar.bz2.sha256` 一致。本次为了速度先从 `mirrors.cloud.tencent.com` 镜像取得归档，再用官方校验值核对；脚本默认仍使用官方 URL，可用 `CAMPUSFLOW_PG_URL` 覆盖。
- 构建后的关键文件哈希也写入 `desktop/postgres/VERSIONS.md`（`postgres`、`initdb`、`pg_ctl`、`pg_isready`、`psql`、`pg_dump`、`pg_restore`、`lib/vector.dylib`）。

许可证：

- PostgreSQL：PostgreSQL License，全文随包保存在 `share/licenses/postgresql-COPYRIGHT`。
- pgvector：PostgreSQL License，全文随包保存在 `share/licenses/pgvector-LICENSE`。
- 两者都允许在保留许可证文本的前提下随二进制分发；`VERSIONS.md` 记录了同样的说明。

## 3. 构建与裁剪

从干净状态执行（先删除暂存目录）：

```bash
rm -rf desktop/vendor/pgsql
desktop/postgres/build_macos.sh
```

最终一次构建输出：

```text
[build] using cached postgresql-17.11.tar.bz2
[build] using cached pgvector-0.8.7.tar.gz
[build] cleaning previous build and staging output
[build] configuring PostgreSQL 17.11 (prefix /opt/campusflow/pgsql)
[build] building PostgreSQL with 10 jobs
[build] building pgvector 0.8.7 against staged PostgreSQL headers
[build] moving staged tree to .../desktop/vendor/pgsql
[build] pruning development files
[build] copying license files
[build] rewriting Mach-O install names for relocation
[build] verifying staged binaries
[build] done: postgres (PostgreSQL) 17.11
[build] pgvector extension files: share/extension/vector* and lib/vector.dylib
```

构建方式与关键选择：

- 直接从源码编译，不使用、也不需要 Homebrew keg。`--prefix=/opt/campusflow/pgsql` 只是构建期占位前缀，最终暂存路径与之无关。
- `configure` 使用 `--without-readline --without-zlib --without-openssl --without-icu`，避免把 Homebrew 的 OpenSSL、ICU、readline 或 zlib 带进运行目录。SCRAM-SHA-256 由 PostgreSQL 自身实现，不依赖 OpenSSL；本地回环连接不需要 TLS。
- PostgreSQL 通过 `make install DESTDIR=<build root>` 安装；pgvector 以暂存树中的 `pg_config` 编译并安装。`pg_config` 会把前缀重定位到自己所在位置，因此 pgvector 安装时不能再传一次 `DESTDIR`，否则路径会叠加（本次构建中曾出现并被修正）。
- 安装后裁剪：删除 `include/`、`lib/pkgconfig/`、`lib/pgxs/`、静态库、`ecpg`、`pg_config`、man/doc 目录；只保留运行、备份与升级前检查需要的可执行文件。
- 保留的扩展文件：`share/extension/vector.control`、`share/extension/vector--*.sql`、`lib/vector.dylib`。PostgreSQL 17 的前缀布局中 `pkglibdir` 就是 `lib/`，因此扩展模块位于 `lib/vector.dylib`，不是 `lib/postgresql/`。

暂存目录结构（19 MB）：

| 路径 | 大小 | 内容 |
| --- | --- | --- |
| `bin/` | 12 MB | `postgres` 8.8 MB，以及 `initdb`、`pg_ctl`、`pg_isready`、`psql`、`pg_dump`、`pg_restore`、`createdb` 等 |
| `lib/` | 4.1 MB | `libpq.5.dylib`（304 KB）、`vector.dylib`（200 KB）、`plpgsql.dylib`、编码转换模块等 |
| `share/` | 3.1 MB | 时区、`postgres.bki`、`extension/vector*`、许可证文件 |

`share/timezone`、`share/timezonesets`、`share/tsearch_data` 保留；没有 `share/locale`（未启用 NLS）。

## 4. 可重定位性与动态库

实现方式：

1. 所有从 `/opt/campusflow/pgsql/...` 引用捆绑 dylib 的 Mach-O，把依赖改写为 `@loader_path` 相对路径：`bin/*` 使用 `@loader_path/../lib/<name>`，`lib/*.dylib` 使用 `@loader_path/<name>`。
2. 把 `lib/*.dylib` 的 install name 改为 `@rpath/<name>`。
3. 对修改过的文件执行 `codesign --force --sign -`（ad-hoc），否则 arm64 macOS 会拒绝加载签名失效的二进制。
4. PostgreSQL 自身按可执行文件位置解析 `share/`，因此复制整个暂存目录即可保持可用。

`otool -L` 抽样（最终产物）：

```text
$ otool -L desktop/vendor/pgsql/bin/postgres
	/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1359.0.0)

$ otool -L desktop/vendor/pgsql/lib/vector.dylib
	/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1359.0.0)

$ otool -L desktop/vendor/pgsql/bin/psql
	@loader_path/../lib/libpq.5.dylib (compatibility version 5.0.0, current version 5.17.0)
	/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1359.0.0)
```

检查结果：

- 44 个 Mach-O 被改写并 ad-hoc 重签；扫描 `bin/` 与 `lib/` 下全部 Mach-O，没有 `/opt/homebrew` 或 `/usr/local/opt` 引用。
- `codesign --verify --strict desktop/vendor/pgsql/bin/psql` 通过。
- 重定位验证：把 `desktop/vendor/pgsql` 复制到 `/tmp/p003-relocated-pgsql`，从 `/` 目录以 `CAMPUSFLOW_PG_HOME=/tmp/p003-relocated-pgsql` 运行完整冒烟，退出码 0（端口 63791）。
- `DYLD_PRINT_LIBRARIES=1 /tmp/p003-relocated-pgsql/bin/psql --version` 显示实际加载 `/private/tmp/p003-relocated-pgsql/lib/libpq.5.dylib`，证明没有回退到构建目录或 Homebrew 路径。
- ad-hoc 签名只满足本机执行；Gatekeeper、公证和安装器签名属于 P009，未在本任务验证。

## 5. 数据目录、日志、端口与密码约定

| 项目 | 约定 |
| --- | --- |
| 数据目录 | 必须位于用户可写目录，不能是应用安装目录。冒烟使用 `/tmp/campusflow-pg-smoke/data`；桌面端应使用类似 `~/Library/Application Support/CampusFlow/pgdata`，由 Electron 主进程创建并保证权限。 |
| 日志 | 与数据目录分开，冒烟写在 `/tmp/campusflow-pg-smoke/logs`；桌面端应写入 `~/Library/Logs/CampusFlow/`。 |
| 端口 | 每次从系统申请一个空闲端口，只设置 `listen_addresses='127.0.0.1'`；端口写入 `postgresql.conf` 与 `postmaster.pid`，后端连接串使用同一端口。 |
| 密码 | `openssl rand -base64 32` 生成随机密码，只写入权限 0600 的临时 pwfile 供 `initdb --pwfile` 使用，初始化后立即删除；脚本不打印密码，并在结束时用实际密码 grep 全部日志确认未泄漏。 |
| 认证 | `--auth=scram-sha-256`，TCP 与 Unix socket 都使用 SCRAM。 |
| 编码与排序 | `--locale=C --encoding=UTF8`；冒烟查询 `server_encoding` 与 `pg_database.datcollate` 验证。 |
| 启动/停止 | `pg_ctl -D <data> -l <log> -w start/stop`；不使用后台守护进程或系统服务。 |

冒烟中的关键初始化命令：

```bash
initdb -D "$DATA_DIR" --locale=C --encoding=UTF8 \
  --auth=scram-sha-256 --username=campusflow --pwfile="$PW_FILE"
```

桌面端在应用重启后需要再次连接时，必须能从安全存储（例如 Keychain）取回密码；本任务只验证初始化与会话内连接，**没有实现密码持久化存储**。

已知的正常日志噪声：`pg_isready` 按设计不提交密码，服务端会记录两次 `FATAL: password authentication failed for user "liyifan"`（初始启动和重启后各一次）。这不是密码泄漏，也不影响就绪判断；日志中不含口令本身。

## 6. 冒烟结果

执行：

```bash
desktop/postgres/smoke_macos.sh   # 第二次运行会清空并重新 initdb
```

两次运行均退出 0，端口随机（63198、63535），以下为关键输出（两次相同）：

```text
[smoke] postgres (PostgreSQL) 17.11
[smoke] random loopback port: 63198 (password: redacted, stored only in a mode-600 pwfile)
[smoke] initdb (locale=C, UTF8, SCRAM-SHA-256)
[smoke] cluster initialized: 17
[smoke] pg_isready OK (pid 13257 in postmaster.pid)
postgres 13257 liyifan 5u IPv4 ... TCP 127.0.0.1:63198 (LISTEN)
[smoke] settings OK (server_encoding=UTF8, lc_collate=C, listen_addresses=127.0.0.1, port=63198, hba=scram-sha-256)
[smoke] CREATE EXTENSION vector OK (extversion 0.8.7)
[smoke] HNSW index present: CREATE INDEX smoke_items_embedding_hnsw ON public.smoke_items USING hnsw (embedding vector_l2_ops)
[smoke] nearest-neighbour query OK (nearest label: near)
[smoke] EXPLAIN uses smoke_items_embedding_hnsw
[smoke] no postgres process remains for /tmp/campusflow-pg-smoke/data
[smoke] restart persistence OK (rows=3, marker=persisted-after-restart, extversion=0.8.7, nearest=near)
[smoke] password audit OK: random password does not appear in logs
[smoke] PASS: initdb, loopback-only start, vector + HNSW, restart persistence, clean stop
```

第二次运行端口为 63535，其余断言相同，`EXIT=0`。

具体覆盖：

- `lsof` 只显示 `127.0.0.1:<port>`；脚本拒绝 `*`、`0.0.0.0`、IPv6 任意地址绑定。
- `postmaster.pid` 中的 PID 存活，`pg_isready` 成功；`SHOW port` 与配置端口一致。
- `CREATE EXTENSION vector` 返回 `extversion 0.8.7`；`pg_indexes.indexdef` 含 `USING hnsw`；关闭 seqscan 后 `EXPLAIN` 使用该 HNSW 索引，最近邻查询返回 `near`。
- 停止后 `ps -axo pid=,command=` 中不存在引用该数据目录的进程，`pg_ctl status` 返回非运行状态，端口不再监听。
- 重启后 3 行数据仍在，标记行 `persisted-after-restart`、扩展版本和最近邻查询均保持。
- 日志目录中搜索实际密码无命中。

重定位冒烟另跑一次（复制到 `/tmp/p003-relocated-pgsql`，从 `/` 启动，端口 63791），同样 `PASS` 且退出 0。

## 7. 后端回归

本任务未修改 `backend/` 任何代码，仍执行完整后端测试：

```text
backend$ uv run --locked pytest
1558 passed, 6 skipped, 9 warnings in 203.96s

backend$ uv run --locked ruff check .
All checks passed!
```

说明：第一次全量测试时工作区外另有多个 worktree 同时使用本机 5432 测试库，出现连接槽耗尽（`remaining connection slots are reserved ...`），导致 1 failed、2 errors；单独重跑这 3 项全部通过，随后单独运行的全量测试如上为全绿。失败与代码改动无关。

## 8. 未验证事项

- macOS x86_64 构建：已由 `desktop-macos-build.yml` 在 `macos-15-intel` 上构建
  PostgreSQL 17.11 + pgvector 并打包 DMG，包内 `pgsql/bin/postgres` 为 x86_64，
  冒烟与 DMG 内容校验通过（见 DESKTOP-E2E-macos.md）；Host 架构（arm64）上未做交叉编译。
- Windows 版本（P003b）。
- 真正没有安装 Xcode/Homebrew 的干净机器：本机具备 Xcode CLT；产物本身不链接 Homebrew，但未在无编译工具的机器上验证。
- 签名、公证、Gatekeeper 首次启动与安装器内的最终路径（P009）；当前只有 ad-hoc 签名。
- Electron 主进程的生命周期监管、退出清理、崩溃恢复、端口冲突重试与应用包内复制步骤。
- 密码在 Keychain 等安全存储中的持久化；本任务密码只存在于初始化进程和冒烟脚本内存中。
- 业务数据库迁移与 1024 维 `vector_cosine_ops` HNSW（P006/P011）；冒烟只验证 `vector(3)` 的 HNSW `vector_l2_ops` 及扩展安装。
- `pg_dump`/`pg_restore` 往返、备份目录权限与备份保留策略（P006）。
- `pg_upgrade` 升级路径：二进制被保留，但未执行跨主版本升级演练。
- `SIGKILL`/断电后的崩溃恢复、fsync 行为；只验证了 `pg_ctl -m fast` 正常停止。
- 多个应用实例同时启动时的端口/数据目录互斥，以及用户目录空间不足、只读目录等异常。
- 非 C locale、非 UTF8 数据库、DST/时区切换等场景。
