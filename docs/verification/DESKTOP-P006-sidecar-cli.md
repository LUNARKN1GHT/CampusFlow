# P006 Sidecar CLI 验收

对应 Issue：#472。实现分支：`issue/#472`（已合入 `issue/#470`／`#471`／`#467` 基线）。

## 交付

- `campusflow/sidecar/`：`serve`、`worker`、`migrate`、`backup`、`restore`、`doctor` 六个子命令，入口登记为 `campusflow-sidecar`（`backend/pyproject.toml` 的 `[project.scripts]`）。
- `migrate` 复用 `core/resources.py` 与 `infrastructure/db/migrations.py`，按显式配置、打包资源、安装包数据、仓库目录解析迁移目录；`--check` 只读。
- `worker --mode auto` 跟随部署模式：`desktop` 走数据库轮询（不依赖 Redis），`server` 走 RQ；两种模式复用 `workers/assembly` 的解析器与向量装配。
- `backup` 生成 tar（`manifest.json` + `pg_dump -Fc` + `storage/`），`restore` 校验 manifest、非空库必须显式 `--replace`、覆盖前写安全备份；已安装的扩展从 `pg_restore` 恢复清单中跳过，避免普通角色执行 `DROP/CREATE EXTENSION`。
- `doctor` 检查数据库、`vector` 扩展、迁移 current/head、目录可写、`pg_dump`/`pg_restore`、模型密钥是否配置（只报告布尔值）与日志目录。
- 每个事件一行 JSON 写 stderr；stdout 只放结果；连接串口令与模型密钥在写出前脱敏。退出码：`0` 成功、`1` 运行失败、`2` 参数错误。
- `workers/main.py` 抽出 `run_server_worker`，供 `python -m` 与 sidecar 共用，未改变 RQ 行为。

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

## 未验证

- Windows 上的 `pg_dump`／`pg_restore` 发现与路径；本地只验证 macOS + Homebrew PostgreSQL 16.14。
- `--strategy auto` 的临时库改名路径：本机 `campusflow` 角色没有 `CREATE DATABASE` 权限，实际回退到 `direct`；该分支仅由单元测试覆盖，未在具备建库权限的实例上执行。
- 直接恢复不是事务性的；中途失败需依赖恢复前安全备份，已在文档中标注。
- 打包后的 sidecar 已切换：`desktop/sidecar_entry.py` 现在只调用 `campusflow.sidecar.cli:main`，spec 收集 `campusflow.sidecar`／`workers`／`infrastructure.queue` 子模块。集成分支重建 91 MB one-folder（46.4 s），在 `/tmp` 的 `env -i` 环境执行 `--help`、`doctor --json` 与 `serve --port 8999`，`/api/v1/health` 返回 200 并优雅退出。
- 真实 Electron 调用与真实视觉/向量模型未验证。
