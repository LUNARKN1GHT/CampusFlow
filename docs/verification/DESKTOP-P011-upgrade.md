# P011 升级迁移、备份与回滚验收

对应 Issue：#477。实现分支：`issue/#477`；合并基线：`integration/desktop-p001-p005`。

## 交付

- `application/upgrade.py`：版本比较、启动门禁、升级编排、失败恢复与保留策略；全部外部动作通过可注入端口完成，便于故障演练。
- `infrastructure/db/schema.py`：从安装包迁移目录读取内置 head／revision 图，读取数据库当前 revision，并以显式数据库地址执行 Alembic 升级。
- `infrastructure/backup/postgres.py`：`pg_dump` 自定义归档、资料目录快照、SHA-256 manifest、校验后恢复、按份数清理旧备份。
- `core/config.py`：新增 `CAMPUSFLOW_BACKUP_RETENTION_COUNT`（默认 5，最小 1）与 `CAMPUSFLOW_PG_BIN`；`CAMPUSFLOW_BACKUP_RETENTION_DAYS` 保留为删除界面的说明字段，不参与升级备份清理。
- 文档：`docs/DEVELOPMENT.md` 增加桌面升级调用顺序与专项测试命令；`.env.example` 增加两个新变量。

## 公开 API（供 sidecar CLI 调用）

```python
from campusflow.application.upgrade import (
    UpgradeState,          # uninitialized | up_to_date | upgrade_required | database_ahead
    DatabaseAheadError,    # 数据库新于应用；拒绝启动，不降级
    UpgradeRequiredError,  # 未初始化或落后；不是可启动状态
    UpgradeFailedError,    # 迁移失败；消息说明是否已恢复及备份位置
    BackupError,           # 备份或恢复失败
    evaluate_upgrade,      # (db_revision, bundled_head, *, known_revisions=None) -> UpgradeState
    inspect_schema,        # (settings, *, head=None, revision=None, known_revisions=None) -> SchemaSnapshot
    check_schema_or_raise, # (settings, *, head=None, revision=None, known_revisions=None) -> UpgradeState
    run_upgrade,           # (settings, *, backup=None, restore=None, migrate=None, head=None,
                           #  revision=None, known_revisions=None, retention_count=None, prune=None)
                           #  -> UpgradeReport
    upgrade_if_needed,     # (settings, **ports) -> UpgradeReport
)
```

基础设施辅助函数：

```python
bundled_head(migrations_dir: str | None = None) -> str
bundled_revisions(migrations_dir: str | None = None) -> frozenset[str]
database_revision(engine: Engine) -> str | None
settings_database_revision(settings: Settings) -> str | None
run_alembic_upgrade(settings: Settings, target_revision: str) -> None
create_pre_upgrade_backup(settings: Settings, *, db_revision: str | None,
                          app_head: str, now: datetime | None = None) -> BackupInfo
restore_pre_upgrade_backup(settings: Settings, backup: BackupInfo) -> None
prune_pre_upgrade_backups(settings: Settings, *, keep: int,
                          current: Path | None) -> tuple[str, ...]
```

可注入端口签名：`backup(settings, *, db_revision, app_head) -> BackupInfo`、
`restore(settings, backup) -> None`、`migrate(settings, target_revision) -> None`、
`head(migrations_dir) -> str`、`revision(settings) -> str | None`、
`prune(settings, *, keep, current) -> tuple[str, ...]`。

sidecar 启动顺序：先 `upgrade_if_needed(settings)`，再 `check_schema_or_raise(settings)`。
后者只在 `up_to_date` 时返回，其余状态均拒绝启动。

## 行为

- `evaluate_upgrade` 只有能证明数据库 revision 属于安装包迁移图（含多个未合并头）时才判定 `upgrade_required`；未知 revision 一律按 `database_ahead` 处理，避免旧应用误改新库。未传 `known_revisions` 时无法证明祖先关系，同样保守拒绝。
- `database_ahead`：不创建备份、不执行迁移、不降级，直接抛中文 `DatabaseAheadError`。
- `uninitialized`：直接迁移建库，没有可备份数据，因此不生成升级前备份。
- `upgrade_required`：先备份到 `<data_dir>/backups/pre-upgrade-<UTC>/`；备份失败即中止，不开始迁移。
- 备份内容：`database.dump`（pg_dump custom）、`materials/`（按存储键复制，不跟随符号链接）、`manifest.json`（格式版本、UTC 时间、应用 head、数据库 revision、dump 大小与 SHA-256、资料文件数／总字节／逐文件 SHA-256、脱敏连接串）。
- 迁移后重新读取 revision，必须等于内置 head；迁移异常或版本不一致都触发恢复。
- 恢复先校验 dump 和每个资料文件的 SHA-256，校验失败不会清空当前数据库；校验通过后清理当前 schema 中非扩展拥有的表、序列、视图、例程和类型，再用过滤后的 pg_restore 清单恢复，跳过 `EXTENSION` 与 `SCHEMA` 条目，避免普通数据库用户尝试修改 `vector` 扩展。恢复后再次核对 revision，并逐文件复核资料校验和。
- 口令只通过 `PGPASSWORD` 等环境变量传给子进程；命令行、manifest、日志和错误文本均不包含口令（连接串显示为 `user:***@host`）。

## 保留策略

升级前备份目录名以 `pre-upgrade-` 开头且含 `manifest.json` 才算候选；按目录名（UTC 时间戳）倒序保留最新 `CAMPUSFLOW_BACKUP_RETENTION_COUNT` 份，并强制保留本次用于恢复的备份。清理只在迁移与校验全部成功后执行；失败路径不删除任何旧备份，恢复所用备份也不会被删除。

## 测试

全量（专用测试库 `campusflow_p011_test`，从全新库迁移后运行）：

```bash
cd backend
CAMPUSFLOW_TEST_DATABASE_URL=postgresql+psycopg://campusflow:campusflow@127.0.0.1:5432/campusflow_p011_test \
  uv run pytest -q
```

```
1599 passed, 6 skipped, 64 warnings in 245.30s (0:04:05)
```

其中新增 `tests/test_upgrade.py`：`14 passed`，覆盖状态矩阵（未初始化／一致／需要升级／数据库更新、多个未合并头、未知 revision）；备份含 dump、资料与逐文件校验和，manifest 无口令；旧 revision 成功升级并保留业务数据；注入迁移在改库后抛错，自动恢复原数据并清除半升级表；损坏备份在校验阶段被拒绝且数据库未被清空；保留策略成功后只留最新 N 份，失败后不清理；数据库更新时未调用备份与迁移；空库不备份直接建表；启动门禁对未初始化、落后、更新三种状态分别拒绝。

`uv run ruff check .`：`All checks passed!`；`uv sync --locked` 无依赖变更。

## 手工端到端（专用 scratch 库）

数据库由本机超级用户预先创建（`campusflow` 角色无 CREATEDB 权限）：

```bash
psql -h 127.0.0.1 -d postgres -c "CREATE DATABASE campusflow_p011_scratch OWNER campusflow;"
psql -h 127.0.0.1 -U liyifan -d campusflow_p011_scratch -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

升级成功（旧 revision → head）：

```bash
CAMPUSFLOW_DATABASE_URL=postgresql+psycopg://campusflow:campusflow@127.0.0.1:5432/campusflow_p011_scratch \
  uv run --locked alembic downgrade n005_web_snapshot_metadata
psql -h 127.0.0.1 -U campusflow -d campusflow_p011_scratch \
  -c "INSERT INTO workspaces (name, timezone) VALUES ('p011-manual-preserve', 'Asia/Shanghai');"
cd backend
CAMPUSFLOW_DATABASE_URL=postgresql+psycopg://campusflow:campusflow@127.0.0.1:5432/campusflow_p011_scratch \
  uv run --locked python - <<'PY'
from campusflow.core.config import Settings
from campusflow.application.upgrade import upgrade_if_needed

settings = Settings(
    _env_file=None,
    database_url="postgresql+psycopg://campusflow:campusflow@127.0.0.1:5432/campusflow_p011_scratch",
    data_dir="/tmp/p011-manual-data",
    storage_dir="/tmp/p011-manual-data/materials",
)
report = upgrade_if_needed(settings)
print("action:", report.action)
print("revision:", report.previous_revision, "->", report.bundled_head)
print("backup:", report.backup.path)
print("manifest revision:", report.backup.database_revision)
print("materials:", report.backup.manifest["materials"]["file_count"])
print("dump sha256:", report.backup.manifest["database"]["sha256"][:16])
PY
```

```
action: upgraded
revision: n005_web_snapshot_metadata -> k001_external_calendar
backup: /private/tmp/p011-manual-data/backups/pre-upgrade-20261021T070432Z
manifest revision: n005_web_snapshot_metadata
materials: 1
dump sha256: f53e8a81451bcd33
```

校验：

```
k001_external_calendar
p011-manual-preserve
f53e8a81451bcd33280a56be9d2c2607ca2ccf76e88fd263ccd2877f46fb4448  .../database.dump
f53e8a81451bcd33280a56be9d2c2607ca2ccf76e88fd263ccd2877f46fb4448 471f56bef16a7fbafe20cb98cf31ea448a1abab914757a4b3f9826a8ba0cdbf2 postgresql+psycopg://campusflow:***@127.0.0.1:5432/campusflow_p011_scratch
```

模拟迁移失败（迁移先建表、插行再抛错）：

```bash
# 先再次 downgrade 到 n005 并插入 p011-manual-failure 标记，然后：
uv run --locked python - <<'PY'
from sqlalchemy import create_engine, text
from campusflow.core.config import Settings
from campusflow.application.upgrade import run_upgrade, UpgradeFailedError

URL = "postgresql+psycopg://campusflow:campusflow@127.0.0.1:5432/campusflow_p011_scratch"
settings = Settings(_env_file=None, database_url=URL, data_dir="/tmp/p011-manual-data",
                    storage_dir="/tmp/p011-manual-data/materials")

def broken_migrate(settings, target):
    engine = create_engine(settings.database_url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE p011_half_upgrade (id integer)"))
        connection.execute(text("INSERT INTO workspaces (name, timezone) VALUES ('p011-half', 'UTC')"))
    engine.dispose()
    raise RuntimeError("manual simulated migration failure")

try:
    run_upgrade(settings, migrate=broken_migrate)
except UpgradeFailedError as exc:
    print("error:", exc)
PY
```

```
error: 数据库迁移失败，已自动恢复升级前的数据库与资料备份。备份保留在 /private/tmp/p011-manual-data/backups/pre-upgrade-20261021T070523Z。
revision: n005_web_snapshot_metadata
preserved: 1
half-workspace: 0
half-table: 0
```

两次备份目录均保留；随后手工 `alembic upgrade head` 恢复 scratch 库。

## 未验证

- Windows 实机：`pg_dump.exe`／`pg_restore.exe` 查找、路径与 Electron 打包后的 sidecar 启动未执行。
- 真实安装器跨版本升级与回滚未执行；当前用同一代码库临时降级／升级模拟旧库。
- 本机 PostgreSQL 服务端与客户端均为 16.14，未在桌面内置 PostgreSQL 17 上复跑；不同主版本的 pg_dump 兼容性只依赖 PostgreSQL 官方约束。
- 备份未加密、未压缩（custom 格式自带压缩），也未上传远端；磁盘不足、超大资料库耗时和中断后的半成品清理未压测。
- sidecar CLI（#472）尚未接入这两个入口；本分支只提供 Python API。
- 用户手动导出备份的界面或命令未实现，当前备份目录可直接复制；诊断包未实现。
- 应用运行中执行恢复的场景未测试；设计前提是 sidecar 在 API 启动前完成升级。
- 非 `public` 的 `search_path` 场景未验证；恢复只清理 `current_schema()` 中的对象。
- 未校验 pg_dump 与数据库主版本差异，版本过旧时由 pg_dump 自身报错并经中文错误包装。
