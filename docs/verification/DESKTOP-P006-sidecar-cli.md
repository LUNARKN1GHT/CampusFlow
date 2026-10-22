# P006 Sidecar CLI 验收

对应 Issue：#472。实现分支：`issue/#472`（已合入 `issue/#470`／`#471`／`#467` 基线）。

## 交付

- `campusflow/sidecar/`：`serve`、`worker`、`migrate`、`backup`、`restore`、`doctor`、`bootstrap`、`export`、`import`、`upgrade` 十个子命令，入口登记为 `campusflow-sidecar`（`backend/pyproject.toml` 的 `[project.scripts]`）。
- `migrate` 复用 `core/resources.py` 与 `infrastructure/db/migrations.py`，按显式配置、打包资源、安装包数据、仓库目录解析迁移目录；`--check` 只读。
- `worker --mode auto` 跟随部署模式：`desktop` 走数据库轮询（不依赖 Redis），`server` 走 RQ；两种模式复用 `workers/assembly` 的解析器与向量装配。
- `backup` 生成 tar（`manifest.json` + `pg_dump -Fc` + `storage/`），`restore` 校验 manifest、非空库必须显式 `--replace`、覆盖前写安全备份；已安装的扩展从 `pg_restore` 恢复清单中跳过，避免普通角色执行 `DROP/CREATE EXTENSION`。
- `doctor` 检查数据库、`vector` 扩展、迁移 current/head、目录可写、`pg_dump`/`pg_restore`、模型密钥是否配置（只报告布尔值）与日志目录；桌面模式额外给出只读的 `bootstrap` 项（`status`、`initialized`、`postgres_running`），不初始化也不启动数据库。
- 每个事件一行 JSON 写 stderr；stdout 只放结果；连接串口令与模型密钥在写出前脱敏。退出码：`0` 成功、`1` 运行失败、`2` 参数错误。
- `workers/main.py` 抽出 `run_server_worker`，供 `python -m` 与 sidecar 共用，未改变 RQ 行为。

### 与 P007／P010／P011 的集成（本轮新增）

只增加装配层，不复制三个模块的步骤：

- `sidecar/bootstrap.py`：仅桌面模式（服务端调用退出码 2）。首次 `initialize`（原子临时目录）后
  `ensure_ready`，之后只 `ensure_ready`；`--check` 只调用 `inspect` 与 `pg_ctl status`。
- `sidecar/transfer.py`：`export_workspace`／`inspect_archive`／`import_archive` 的 JSON 序列化与
  退出码折算；业务异常转成 `SidecarError`，保留模块中文文案。
- `sidecar/upgrade.py`：`inspect_schema`／`upgrade_if_needed` 的只读检查与升级执行；不降级，
  已有数据时迁移前必须成功写备份（P011 保证），失败消息带恢复备份路径。
- `cli.py`：新增 `_emit_payload`（JSON 只兜底替换模型密钥，不做 URL 级脱敏），只有
  `bootstrap --json` 会输出连接串与本地口令。
- `doctor.py`：桌面模式追加只读 `bootstrap` 检查项（非关键项，不改变原有退出码语义）。

命令契约：

| 命令 | 关键参数 | 成功退出 | 失败退出 | stdout |
| --- | --- | --- | --- | --- |
| `bootstrap` | `--json`、`--check` | 0（`--check`：`initialized` 为真时 0） | 1（未就绪／初始化失败，失败消息含诊断日志目录）；服务端模式 2 | JSON（`--json`）或中文状态 |
| `export` | `--output`（必填）、`--workspace-id`（会被拒绝） | 0 | 1 | 一个 JSON 对象 |
| `import` | `archive`、`--inspect`、`--replace`、`--confirm-token` | 0 | 1（缺/错令牌、非空未 `--replace`、归档损坏）；`--inspect` 与写参数同用 2 | 一个 JSON 对象 |
| `upgrade` | `--check`、`--apply`、`--json` | 0（`up_to_date`／`uninitialized`／实际升级成功） | 1（`upgrade_required` 未 `--apply`、`database_ahead`、升级失败） | JSON 或中文摘要 |

`bootstrap --json` 字段：`ready`、`status`、`initialized`（本次是否执行初始化）、`data_dir`、
`database_url`、`local_username`、`local_password`、`schema_head`、`secret_backend`、`warnings`、
`messages`、`started_postgres`、`postgres_running`。口令只出现在这一次的 stdout；stderr 事件、
`--log-file`、`state.json` 与异常消息都不包含口令。`--check` 的 `ready` 固定为 `false`：不连接
数据库就不冒充就绪，只用 `initialized` 表示数据目录可以启动。

确认令牌只接受命令行 `--confirm-token`，**不读取** `CAMPUSFLOW_IMPORT_CONFIRM_TOKEN` 等环境
变量，避免令牌经环境、子进程或日志意外扩散；用户必须先看到 `--inspect` 的条目数、排除项与
令牌，再显式传回。

## 验证证据

- 后端全量（独立测试库 `campusflow_p006_test`，PostgreSQL 16.14 + pgvector 0.8.7）：`1593 passed, 6 skipped`。
- `backend/tests/test_sidecar_cli.py`：10 passed，覆盖迁移幂等与 `--check`、备份/恢复往返与行数/文件一致、manifest 不含口令、`doctor` 正反例与密钥脱敏、参数错误退出码 2、运行失败退出码 1。
- `uv run --locked ruff check .`：`All checks passed!`。
- `campusflow-sidecar doctor --json`（已迁移库）：

```
"ok": true,
"checks": {
  "database": {"ok": true, "critical": true, "status": "connected"},
  "vector_extension": {"ok": true, "critical": true, "version": "0.8.7"},
  "migrations": {"ok": true, "critical": true,
                 "current": ["k001_external_calendar"], "head": ["k001_external_calendar"]},
  "pg_tools": {"ok": true}
}
```

- 备份 → 恢复到另一个空库：

```
备份完成：/tmp/cf-p006-backup.tar（358400 字节，资料文件 67 个，迁移头 k001_external_calendar）
恢复完成：campusflow_p006_restored（direct，资料文件 67 个）
campusflow_p006_final   tables=45  rev=k001_external_calendar
campusflow_p006_restored tables=45  rev=k001_external_calendar
```

恢复目标库名与归档不同名时记录 `restore_name_mismatch` 警告，便于排查换机恢复。

### 本轮（P007／P010／P011 集成）证据

- `uv sync --locked`：`Resolved 53 packages`／`Checked 51 packages`，锁文件未变。
- 后端全量（隔离测试库 `campusflow_p006_cli_test`，PostgreSQL 16.14 + pgvector 0.8.7，管理员连接
  使用本机 OS 超级用户建库）：`1725 passed, 8 skipped`（本分支基线为 `1712 passed, 8 skipped`）。
- `uv run ruff check .`：`All checks passed!`。
- 新增 `backend/tests/test_sidecar_integration.py`：13 passed，覆盖：
  - `bootstrap --check` 空目录退出 1 且不创建目录；服务端模式退出 2。
  - `bootstrap --json` 用真实 initdb 初始化：`ready=true`、`started_postgres=true`、迁移头一致；
    本地口令与数据库口令在 stdout 各出现一次（`out.count(secret) == 1`），在 stderr、`--log-file`
    与 `state.json` 中各出现 0 次；`secrets.json` 权限 0600 且口令一致。
  - 第二次运行 `initialized=false`、`started_postgres=false`，连接与口令不变；非 JSON 输出不含口令；
    停止集群后 `--check` 报告 `postgres_running=false`，再次运行会重新启动（`started_postgres=true`）。
  - `export` → `import --inspect` → `import --confirm-token` → 非空拒绝 → `--replace` 全链路：
    哨兵任务标题与资料文件内容一致，条目数与清单一致，归档字节不含模型密钥；缺令牌、错令牌在
    写入前被拒绝且目标库未创建；`--workspace-id` 被拒绝。
  - `upgrade --check`：`up_to_date` 0；`uninitialized` 0 并提示 bootstrap；`upgrade_required` 1
    且 `alembic_version` 不变；`database_ahead` 1 且不降级；`--check --apply` 在真实落后库上
    生成 `manifest.json`＋`database.dump` 备份后迁移到 head；伪造 migrate 端口失败时退出 1 并
    在 stderr 给出恢复备份路径；P011 错误文案中的连接口令被替换为 `***`。
  - 参数错误（缺 `--output`、`--inspect` 与 `--replace` 同用、未知参数）退出 2。
- 手工闭环（本机 Homebrew PostgreSQL 16.14，临时数据目录 `/tmp/cfp006-manual/data`）：

```
$ campusflow-sidecar bootstrap --check --json       # 空目录
{"ready": false, "status": "uninitialized", "initialized": false, "data_dir": "...",
 "database_url": null, ..., "started_postgres": false, "postgres_running": false}
exit=1

$ campusflow-sidecar bootstrap --json               # 首次（文档中口令以 <redacted> 代替）
{"ready": true, "status": "initialized", "initialized": true, "data_dir": "/tmp/cfp006-manual/data",
 "database_url": "postgresql+psycopg://campusflow:<redacted>@127.0.0.1:58213/campusflow",
 "local_username": "campusflow", "local_password": "<redacted>",
 "schema_head": "k001_external_calendar", "secret_backend": "file", "warnings": [],
 "messages": ["数据库已就绪"], "started_postgres": true, "postgres_running": true}
exit=0

$ campusflow-sidecar bootstrap --json               # 第二次
{"ready": true, "status": "initialized", "initialized": false, ...,
 "started_postgres": false, "postgres_running": true}
exit=0

$ campusflow-sidecar doctor --json                  # 桌面模式
"database": {"ok": true, "status": "connected"},
"vector_extension": {"ok": true, "version": "0.8.7"},
"migrations": {"ok": true, "current": ["k001_external_calendar"], "head": ["k001_external_calendar"]},
"bootstrap": {"ok": true, "critical": false, "status": "initialized",
              "initialized": true, "postgres_running": true}

$ campusflow-sidecar export --output /tmp/cfp006-manual/evidence.campusflow
{"path": "/tmp/cfp006-manual/evidence.campusflow", "size": 207127,
 "sha256": "91c5905bd8f0847c51065f8bbb6432b8bbcebc5730e944f8bfca202c34ed1257",
 "confirm_token": "sha256:91c5905b...", "material_files": 1,
 "counts": {"workspaces": 1, "materials": 1, "material_versions": 1,
            "candidates": 1, "tasks": 1, ...}}
exit=0

$ campusflow-sidecar import /tmp/cfp006-manual/evidence.campusflow --inspect
{"path": "/private/tmp/cfp006-manual/evidence.campusflow", "schema_head": "k001_external_calendar",
 "archive_size": 207127, "confirm_token": "sha256:91c5905b...", "requires_migration": false,
 "material_count": 1, "counts": {..., "tasks": 1}}
exit=0

$ campusflow-sidecar import ... --confirm-token sha256:91c5905b...   # 目标非空、未 --replace
目标数据库 campusflow_manual_import_test 已有数据（... tasks 1）。导入会整体替换本机数据：
确认后请以 replace=True 重新提交同一确认令牌。
exit=1

$ campusflow-sidecar import ... --confirm-token sha256:91c5905b... --replace
{"migrated_from": "k001_external_calendar", "migrated_to": "k001_external_calendar",
 "replaced_database": "campusflow_manual_import_test_pre_import_20261021T083213Z_8002",
 "backup_archive": ".../pre-import-20261021T083213Z-8002.campusflow",
 "restored_counts": {"workspaces": 1, "materials": 1, "tasks": 1, ...}}
exit=0
哨兵任务 "P006 哨兵任务：导出必须保留" 在导入后仍存在。

$ campusflow-sidecar upgrade --check --json
{"state": "up_to_date", "database_revision": "k001_external_calendar",
 "bundled_head": "k001_external_calendar", "requires_bootstrap": false,
 "requires_upgrade": false, "action": null, "backup": null, "duration_seconds": null}
exit=0

$ campusflow-sidecar upgrade --json                 # 已是最新：不建备份
{"state": "up_to_date", ..., "action": "none", "backup": null, "pruned_backups": [],
 "duration_seconds": 0.036}
exit=0
```

## 未验证

- 真实 Electron 外壳调用这四个新命令：本轮按要求没有修改 `desktop/electron/`，命令契约按
  Electron 读取 stdout JSON 的方式设计，但未接入壳层做端到端验证。
- Windows 上的 `pg_dump`／`pg_restore`、`initdb` 与数据目录路径；本地只验证 macOS + Homebrew
  PostgreSQL 16.14。
- `bootstrap` 在 macOS 钥匙串（`secret_backend=auto`）下的读写与首次解锁提示：自动化用例使用
  `CAMPUSFLOW_SECRET_BACKEND=file`，与 P007 的模块用例一致；真实钥匙串路径由 P007 单独验证。
- `--strategy auto` 的临时库改名路径：本机 `campusflow` 角色没有 `CREATE DATABASE` 权限，实际回退到 `direct`；该分支仅由单元测试覆盖，未在具备建库权限的实例上执行。
- 直接恢复不是事务性的；中途失败需依赖恢复前安全备份，已在文档中标注。
- 打包后的 sidecar 已切换：`desktop/sidecar_entry.py` 现在只调用 `campusflow.sidecar.cli:main`，spec 收集 `campusflow.sidecar`／`workers`／`infrastructure.queue` 子模块。集成分支重建 91 MB one-folder（46.4 s），在 `/tmp` 的 `env -i` 环境执行 `--help`、`doctor --json` 与 `serve --port 8999`，`/api/v1/health` 返回 200 并优雅退出。新增子命令尚未在打包产物中复测（入口仍是同一个 `main`）。
- 真实视觉/向量模型未验证。
