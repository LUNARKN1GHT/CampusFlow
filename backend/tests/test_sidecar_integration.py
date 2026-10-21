"""P006：sidecar 的 bootstrap／export／import／upgrade 命令契约。

覆盖：

- `bootstrap --check` 只读；`bootstrap --json` 在临时数据目录上用本机 PostgreSQL
  真正初始化，口令只出现一次在 stdout，stderr、日志文件与 state.json 都没有；
  第二次运行幂等，停止集群后再运行会重新启动。
- `export` → `import --inspect` → `import --confirm-token` → `--replace` 全链路，
  哨兵任务与资料文件存活，归档不含模型密钥；缺令牌、错令牌、非空目标未 `--replace`
  都退出码 1 且不改数据。
- `upgrade --check` 的 up_to_date／uninitialized／upgrade_required／database_ahead
  四种状态与退出码；迁移端口失败时输出恢复备份路径。
- 参数错误退出码 2。

数据库用例使用临时库（名称以 `_test` 结尾）并在结束后删除；没有可建库的管理员
连接时相关用例跳过。桌面首启用例在缺少 `initdb` 时跳过。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, insert, text
from sqlalchemy.engine import make_url

from campusflow.application.upgrade import BackupInfo, run_upgrade
from campusflow.core.config import Settings
from campusflow.infrastructure.db.migrations import build_alembic_config
from campusflow.infrastructure.db.models import (
    Course,
    ExtractedItem,
    Material,
    MaterialVersion,
    Semester,
    Task,
    Workspace,
)
from campusflow.infrastructure.db.postgres_cluster import PgBinaries
from campusflow.sidecar import cli
from campusflow.sidecar import upgrade as upgrade_cli
from campusflow.sidecar.dbtools import script_heads

BACKEND_DIR = Path(__file__).resolve().parent.parent
BASE_DATABASE_URL = os.environ.get(
    "CAMPUSFLOW_TEST_DATABASE_URL",
    "postgresql+psycopg://campusflow:campusflow@127.0.0.1:5432/campusflow_test",
)
ADMIN_DATABASE_URL = os.environ.get(
    "CAMPUSFLOW_TEST_ADMIN_DATABASE_URL", "postgresql+psycopg:///postgres"
)
MODEL_KEY = "sk-p006b-distinctive-model-key"
PG_AVAILABLE = PgBinaries.available()
PG_TOOLS_AVAILABLE = bool(shutil.which("pg_dump") and shutil.which("pg_restore"))
LOCAL_PASSWORD_ENV = "CAMPUSFLOW_LOCAL_PASSWORD"


def _head_revision(settings: Settings | None = None) -> str:
    heads = script_heads(settings or Settings(_env_file=None))
    assert len(heads) == 1
    return heads[0]


def _url_for(database: str) -> str:
    return make_url(BASE_DATABASE_URL).set(database=database).render_as_string(hide_password=False)


def _admin_engine(database: str = "postgres"):
    return create_engine(
        make_url(ADMIN_DATABASE_URL).set(database=database).render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 3},
    )


def _admin_database_names(prefix: str) -> list[str]:
    engine = _admin_engine()
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT datname FROM pg_database WHERE starts_with(datname, :prefix)"),
                {"prefix": prefix},
            )
            return [row[0] for row in rows]
    finally:
        engine.dispose()


def _drop_database(name: str) -> None:
    try:
        engine = _admin_engine()
        try:
            with engine.connect() as connection:
                connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        finally:
            engine.dispose()
    except Exception:
        pass


@dataclass
class ScratchDatabase:
    name: str
    url: str


def _create_database(*, with_vector: bool = True) -> ScratchDatabase:
    name = f"campusflow_p006x_{uuid.uuid4().hex[:8]}_test"
    owner = make_url(BASE_DATABASE_URL).username or "campusflow"
    try:
        engine = _admin_engine()
        try:
            with engine.connect() as connection:
                connection.execute(text(f'CREATE DATABASE "{name}" OWNER "{owner}"'))
        finally:
            engine.dispose()
    except Exception:
        pytest.skip("需要可建库的本地 PostgreSQL 管理员连接才能运行临时库用例")
    if with_vector:
        engine = _admin_engine(name)
        try:
            with engine.connect() as connection:
                connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        finally:
            engine.dispose()
    return ScratchDatabase(name=name, url=_url_for(name))


@pytest.fixture
def database_factory() -> Iterator[object]:
    created: list[ScratchDatabase] = []

    def factory(*, with_vector: bool = True) -> ScratchDatabase:
        database = _create_database(with_vector=with_vector)
        created.append(database)
        return database

    yield factory
    for database in created:
        _drop_database(database.name)


def _use_server_database(
    monkeypatch: pytest.MonkeyPatch, database: ScratchDatabase, storage: Path
) -> None:
    storage.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CAMPUSFLOW_DATABASE_URL", database.url)
    monkeypatch.setenv("CAMPUSFLOW_STORAGE_DIR", str(storage))
    monkeypatch.setenv("CAMPUSFLOW_DEPLOYMENT_MODE", "server")
    monkeypatch.delenv("CAMPUSFLOW_DATA_DIR", raising=False)
    monkeypatch.delenv("CAMPUSFLOW_PG_ADMIN_URL", raising=False)


def _alembic_revision(url: str) -> str | None:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            if (
                connection.execute(text("SELECT to_regclass('public.alembic_version')")).scalar()
                is None
            ):
                return None
            return connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
    finally:
        engine.dispose()


def _set_alembic_revision(url: str, revision: str) -> None:
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num = :value"), {"value": revision}
            )
    finally:
        engine.dispose()


def _table_counts(url: str) -> dict[str, int]:
    tables = ("workspaces", "materials", "material_versions", "extracted_items", "tasks")
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return {
                table: connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
                for table in tables
            }
    finally:
        engine.dispose()


def _task_titles(url: str) -> list[str]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return [
                row[0] for row in connection.execute(text("SELECT title FROM tasks ORDER BY id"))
            ]
    finally:
        engine.dispose()


def _seed_sentinel(database: ScratchDatabase, storage: Path) -> None:
    """写入哨兵任务、候选与资料文件，导入后必须原样存在。"""
    target = storage / "2026/10/sentinel.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"p006b sentinel material\n")

    engine = create_engine(database.url)
    try:
        with engine.begin() as connection:
            workspace_id = connection.execute(
                insert(Workspace.__table__).values(name="P006b 哨兵空间", is_default=True)
            ).inserted_primary_key[0]
            semester_id = connection.execute(
                insert(Semester.__table__).values(
                    workspace_id=workspace_id,
                    name="2026 秋",
                    start_date=date(2026, 9, 1),
                    end_date=date(2027, 1, 15),
                )
            ).inserted_primary_key[0]
            course_id = connection.execute(
                insert(Course.__table__).values(
                    workspace_id=workspace_id, semester_id=semester_id, name="数据结构"
                )
            ).inserted_primary_key[0]
            material_id = connection.execute(
                insert(Material.__table__).values(
                    workspace_id=workspace_id,
                    semester_id=semester_id,
                    course_id=course_id,
                    title="哨兵资料",
                    source_type="paste",
                )
            ).inserted_primary_key[0]
            version_id = connection.execute(
                insert(MaterialVersion.__table__).values(
                    material_id=material_id,
                    version_no=1,
                    storage_key="2026/10/sentinel.txt",
                    checksum="0" * 64,
                )
            ).inserted_primary_key[0]
            connection.execute(
                insert(ExtractedItem.__table__).values(
                    workspace_id=workspace_id, source_version_id=version_id, kind="task"
                )
            )
            connection.execute(
                insert(Task.__table__).values(
                    workspace_id=workspace_id,
                    course_id=course_id,
                    title="P006b 哨兵任务：导入后必须存活",
                )
            )
    finally:
        engine.dispose()


# --------------------------------------------------------------------------- 桌面首启


def _short_data_root() -> Path:
    base = Path("/tmp") if Path("/tmp").is_dir() else Path(tempfile.gettempdir())
    return Path(tempfile.mkdtemp(prefix="cfs-p006b-", dir=base))


def _stop_cluster(pgdata: Path) -> None:
    if not (pgdata / "PG_VERSION").is_file():
        return
    try:
        binaries = PgBinaries.discover()
    except Exception:
        return
    subprocess.run(
        [str(binaries.tool("pg_ctl")), "-D", str(pgdata), "-m", "fast", "-w", "-t", "20", "stop"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


@pytest.fixture
def desktop_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """临时桌面数据目录；结束时停止集群并删除目录。"""
    base = _short_data_root()
    root = base / "data"
    binaries = PgBinaries.discover()
    monkeypatch.setenv("CAMPUSFLOW_DEPLOYMENT_MODE", "desktop")
    monkeypatch.setenv("CAMPUSFLOW_DATA_DIR", str(root))
    monkeypatch.setenv("CAMPUSFLOW_SECRET_BACKEND", "file")
    monkeypatch.setenv("CAMPUSFLOW_PG_BIN", str(binaries.bindir))
    for name in (
        "CAMPUSFLOW_DATABASE_URL",
        "CAMPUSFLOW_STORAGE_DIR",
        "CAMPUSFLOW_BACKUP_DIR",
        "CAMPUSFLOW_PG_ADMIN_URL",
        LOCAL_PASSWORD_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    try:
        yield root
    finally:
        _stop_cluster(root / "pgdata")
        shutil.rmtree(base, ignore_errors=True)


def _secret_file(root: Path) -> dict[str, str]:
    return json.loads((root / "secrets.json").read_text(encoding="utf-8"))


def _log_text(root: Path) -> str:
    chunks = []
    for path in (root / "logs").glob("*"):
        if path.is_file():
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


@pytest.mark.skipif(not PG_AVAILABLE, reason="本机缺少 initdb／pg_ctl／pg_isready")
def test_bootstrap_check_on_empty_dir_creates_nothing(desktop_env: Path, capsys) -> None:
    assert not desktop_env.exists()

    assert cli.main(["bootstrap", "--check", "--json"]) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["ready"] is False
    assert payload["status"] == "uninitialized"
    assert payload["initialized"] is False
    assert payload["data_dir"] == str(desktop_env)
    assert payload["database_url"] is None
    assert payload["local_password"] is None
    assert not desktop_env.exists(), "--check 不得创建数据目录"


def test_bootstrap_rejects_server_mode(capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAMPUSFLOW_DEPLOYMENT_MODE", "server")
    monkeypatch.delenv("CAMPUSFLOW_DATA_DIR", raising=False)
    assert cli.main(["bootstrap", "--check"]) == 2
    assert "桌面模式" in capsys.readouterr().err


@pytest.mark.skipif(not PG_AVAILABLE, reason="本机缺少 initdb／pg_ctl／pg_isready")
def test_bootstrap_json_keeps_password_on_stdout_only(
    desktop_env: Path, capsys, tmp_path: Path
) -> None:
    log_path = tmp_path / "sidecar.log"
    assert cli.main(["bootstrap", "--json", "--log-file", str(log_path)]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["ready"] is True
    assert payload["status"] == "initialized"
    assert payload["initialized"] is True
    assert payload["started_postgres"] is True
    assert payload["postgres_running"] is True
    assert payload["data_dir"] == str(desktop_env)
    assert payload["schema_head"] == _head_revision()
    assert payload["secret_backend"] == "file"
    assert payload["local_username"] == "campusflow"

    connection = make_url(payload["database_url"])
    assert connection.database == "campusflow"
    assert connection.username == "campusflow"
    db_password = connection.password
    local_password = payload["local_password"]
    assert db_password and len(db_password) >= 32
    assert local_password and len(local_password) >= 32
    assert db_password != local_password

    # 口令只在 stdout 的 JSON 里出现一次；stderr、日志文件与 state.json 都没有。
    assert captured.out.count(local_password) == 1
    assert captured.out.count(db_password) == 1
    state_text = (desktop_env / "state.json").read_text(encoding="utf-8")
    log_file_text = log_path.read_text(encoding="utf-8")
    for secret in (db_password, local_password):
        assert secret not in captured.err
        assert secret not in log_file_text
        assert secret not in state_text
    assert _secret_file(desktop_env)["local-password"] == local_password
    if os.name != "nt":
        assert stat.S_IMODE((desktop_env / "secrets.json").stat().st_mode) == 0o600


@pytest.mark.skipif(not PG_AVAILABLE, reason="本机缺少 initdb／pg_ctl／pg_isready")
def test_bootstrap_is_idempotent_and_restarts_stopped_cluster(desktop_env: Path, capsys) -> None:
    assert cli.main(["bootstrap", "--json"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["initialized"] is True
    assert first["started_postgres"] is True

    assert cli.main(["bootstrap", "--json"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["initialized"] is False
    assert second["started_postgres"] is False
    assert second["postgres_running"] is True
    assert second["ready"] is True
    assert second["database_url"] == first["database_url"]
    assert second["local_password"] == first["local_password"]

    # 非 JSON 输出只给中文状态，不出现本地口令。
    assert cli.main(["bootstrap"]) == 0
    text = capsys.readouterr()
    assert "已就绪" in text.out
    assert first["local_password"] not in text.out

    # 集群被停止后，再次运行必须把它重新启动。
    _stop_cluster(desktop_env / "pgdata")
    assert cli.main(["bootstrap", "--check", "--json"]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["initialized"] is True
    assert checked["postgres_running"] is False
    assert checked["ready"] is False  # 只读检查不冒充数据库已验证

    assert cli.main(["bootstrap", "--json"]) == 0
    restarted = json.loads(capsys.readouterr().out)
    assert restarted["started_postgres"] is True
    assert restarted["postgres_running"] is True
    assert restarted["ready"] is True


# --------------------------------------------------------------------------- 导出与导入


@pytest.mark.skipif(not PG_TOOLS_AVAILABLE, reason="需要 pg_dump／pg_restore 才能验收迁移包")
def test_export_inspect_import_round_trip(
    database_factory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    source = database_factory()
    storage = tmp_path / "source-materials"
    _use_server_database(monkeypatch, source, storage)
    monkeypatch.setenv("CAMPUSFLOW_BACKUP_DIR", str(tmp_path / "source-backups"))
    monkeypatch.setenv("ZHIPU_API_KEY", MODEL_KEY)
    assert cli.main(["migrate"]) == 0
    _seed_sentinel(source, storage)
    source_counts = _table_counts(source.url)
    capsys.readouterr()

    archive = tmp_path / "workspace.campusflow"
    assert cli.main(["export", "--output", str(archive)]) == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported["path"] == str(archive)
    assert exported["size"] == archive.stat().st_size
    assert exported["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert exported["confirm_token"] == f"sha256:{exported['sha256']}"
    assert exported["material_files"] == 1
    assert exported["counts"]["tasks"] == 1
    assert exported["counts"]["candidates"] == 1
    assert MODEL_KEY.encode() not in archive.read_bytes()

    # --workspace-id 由 P010 明确拒绝：整库导出，不做裁剪。
    rejected = tmp_path / "rejected.campusflow"
    assert cli.main(["export", "--output", str(rejected), "--workspace-id", "1"]) == 1
    assert "工作空间" in capsys.readouterr().err
    assert not rejected.exists()

    # --inspect 只读：返回确认令牌、条目数与排除项。
    assert cli.main(["import", str(archive), "--inspect"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["confirm_token"] == exported["confirm_token"]
    assert info["counts"] == exported["counts"]
    assert info["material_count"] == 1
    assert info["requires_migration"] is False
    excluded = {item["name"] for item in info["excluded_credentials"]}
    assert {"calendar_connections", "CAMPUSFLOW_LOCAL_PASSWORD", "session_cookie"} <= excluded
    assert _table_counts(source.url) == source_counts

    target_name = f"campusflow_p006x_{uuid.uuid4().hex[:8]}_test"
    target_storage = tmp_path / "target-materials"
    target_url = _url_for(target_name)
    monkeypatch.setenv("CAMPUSFLOW_DATABASE_URL", target_url)
    monkeypatch.setenv("CAMPUSFLOW_STORAGE_DIR", str(target_storage))
    monkeypatch.setenv("CAMPUSFLOW_BACKUP_DIR", str(tmp_path / "target-backups"))
    monkeypatch.setenv("CAMPUSFLOW_PG_ADMIN_URL", ADMIN_DATABASE_URL)
    try:
        # 缺令牌与错令牌都在写入前拒绝。
        assert cli.main(["import", str(archive)]) == 1
        assert "确认令牌" in capsys.readouterr().err
        assert not _admin_database_names(target_name), "缺令牌不得创建目标库"
        assert cli.main(["import", str(archive), "--confirm-token", "sha256:wrong"]) == 1
        assert "不一致" in capsys.readouterr().err
        assert not _admin_database_names(target_name)

        assert cli.main(["import", str(archive), "--confirm-token", info["confirm_token"]]) == 0
        imported = json.loads(capsys.readouterr().out)
        assert imported["restored_counts"] == exported["counts"]
        assert imported["migrated_to"] == _head_revision()
        assert _task_titles(target_url) == ["P006b 哨兵任务：导入后必须存活"]
        assert (target_storage / "2026/10/sentinel.txt").read_bytes() == (
            storage / "2026/10/sentinel.txt"
        ).read_bytes()

        # 非空目标没有 --replace：拒绝且数据不变。
        before = _table_counts(target_url)
        assert cli.main(["import", str(archive), "--confirm-token", info["confirm_token"]]) == 1
        assert "已有数据" in capsys.readouterr().err
        assert _table_counts(target_url) == before

        assert (
            cli.main(
                [
                    "import",
                    str(archive),
                    "--confirm-token",
                    info["confirm_token"],
                    "--replace",
                ]
            )
            == 0
        )
        replaced = json.loads(capsys.readouterr().out)
        assert replaced["replaced_database"]
        assert replaced["backup_archive"] and Path(replaced["backup_archive"]).is_file()
        assert _task_titles(target_url) == ["P006b 哨兵任务：导入后必须存活"]
    finally:
        for name in _admin_database_names("campusflow_p006x_"):
            if name == target_name or name.startswith(f"{target_name}_pre_import_"):
                _drop_database(name)


# --------------------------------------------------------------------------- 升级


def test_upgrade_check_up_to_date_exits_zero(
    database_factory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    database = database_factory()
    _use_server_database(monkeypatch, database, tmp_path / "materials")
    assert cli.main(["migrate"]) == 0
    capsys.readouterr()

    assert cli.main(["upgrade", "--check", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "up_to_date"
    assert payload["database_revision"] == _head_revision()
    assert payload["bundled_head"] == _head_revision()
    assert payload["action"] is None
    assert _alembic_revision(database.url) == _head_revision()

    # 已是最新时 --check --apply 也不写库。
    assert cli.main(["upgrade", "--check", "--apply", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["action"] is None

    assert cli.main(["upgrade", "--check"]) == 0
    assert "已是最新" in capsys.readouterr().out


def test_upgrade_check_uninitialized_reports_bootstrap(
    database_factory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    database = database_factory()
    _use_server_database(monkeypatch, database, tmp_path / "materials")

    assert cli.main(["upgrade", "--check", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "uninitialized"
    assert payload["requires_bootstrap"] is True
    assert _alembic_revision(database.url) is None

    assert cli.main(["upgrade", "--check"]) == 0
    assert "bootstrap" in capsys.readouterr().out


def test_upgrade_check_upgrade_required_exits_one_without_writing(
    database_factory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    database = database_factory()
    _use_server_database(monkeypatch, database, tmp_path / "materials")
    assert cli.main(["migrate"]) == 0
    config = build_alembic_config(Settings(_env_file=None).migrations_dir)
    script = ScriptDirectory.from_config(config)
    revisions = sorted(script.get_bases())
    assert revisions, "迁移脚本至少有一个基线版本"
    stale = revisions[0]
    if stale == _head_revision():
        pytest.skip("当前迁移只有一个版本，无法构造落后状态")
    _set_alembic_revision(database.url, stale)
    capsys.readouterr()

    assert cli.main(["upgrade", "--check", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "upgrade_required"
    assert payload["database_revision"] == stale
    assert payload["requires_upgrade"] is True
    assert _alembic_revision(database.url) == stale, "--check 不得写库"

    assert cli.main(["upgrade", "--check"]) == 1
    err = capsys.readouterr()
    assert "需要升级" in err.out
    assert "--apply" in err.out


def test_upgrade_check_database_ahead_refuses_and_keeps_revision(
    database_factory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    database = database_factory()
    _use_server_database(monkeypatch, database, tmp_path / "materials")
    assert cli.main(["migrate"]) == 0
    _set_alembic_revision(database.url, "zzz_from_newer_app")
    capsys.readouterr()

    assert cli.main(["upgrade", "--check", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "database_ahead"
    assert _alembic_revision(database.url) == "zzz_from_newer_app"

    # 默认升级同样拒绝，不降级、不迁移。
    assert cli.main(["upgrade"]) == 1
    assert "高于" in capsys.readouterr().err
    assert _alembic_revision(database.url) == "zzz_from_newer_app"


@pytest.mark.skipif(not PG_TOOLS_AVAILABLE, reason="需要 pg_dump 才能验证升级前备份")
def test_upgrade_check_apply_backs_up_before_migrating(
    database_factory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    database = database_factory()
    storage = tmp_path / "materials"
    _use_server_database(monkeypatch, database, storage)
    monkeypatch.setenv("CAMPUSFLOW_BACKUP_DIR", str(tmp_path / "backups"))
    settings = Settings(_env_file=None)
    config = build_alembic_config(settings.migrations_dir)
    revisions = sorted(ScriptDirectory.from_config(config).get_bases())
    assert revisions
    stale = revisions[0]
    if stale == _head_revision():
        pytest.skip("当前迁移只有一个版本，无法构造落后状态")
    command.upgrade(config, stale)
    assert _alembic_revision(database.url) == stale
    capsys.readouterr()

    assert cli.main(["upgrade", "--check", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "upgrade_required"

    assert cli.main(["upgrade", "--check", "--apply", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "upgraded"
    assert payload["database_revision"] == stale
    assert payload["bundled_head"] == _head_revision()
    assert payload["duration_seconds"] is not None
    backup = Path(payload["backup"])
    assert backup.is_dir()
    assert (backup / "manifest.json").is_file()
    assert (backup / "database.dump").is_file()
    assert _alembic_revision(database.url) == _head_revision()


def test_upgrade_failure_prints_recovery_backup_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    backup_path = tmp_path / "pre-upgrade-fake"

    def fake_upgrade(settings: Settings):
        return run_upgrade(
            settings,
            head=lambda _: "head_new",
            revision=lambda _: "head_old",
            known_revisions={"head_old", "head_new"},
            backup=lambda _settings, *, db_revision, app_head: BackupInfo(
                path=backup_path, manifest={}
            ),
            migrate=lambda _settings, _target: (_ for _ in ()).throw(RuntimeError("boom")),
            restore=lambda _settings, _backup: None,
            prune=lambda _settings, *, keep, current: (),
        )

    monkeypatch.setattr(upgrade_cli, "upgrade_if_needed", fake_upgrade)

    assert cli.main(["upgrade"]) == 1
    err = capsys.readouterr().err
    assert "已自动恢复" in err
    assert str(backup_path) in err


# --------------------------------------------------------------------------- 参数


def test_usage_errors_return_exit_code_two(capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAMPUSFLOW_DEPLOYMENT_MODE", "server")
    monkeypatch.delenv("CAMPUSFLOW_DATA_DIR", raising=False)
    assert cli.main(["bootstrap", "--check"]) == 2
    assert cli.main(["export"]) == 2
    assert cli.main(["import", "archive.campusflow", "--inspect", "--replace"]) == 2
    assert cli.main(["upgrade", "--bogus"]) == 2
    assert "只读检查" in capsys.readouterr().err


def test_upgrade_scrubs_database_password(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """P011 的错误文案经兜底脱敏后不得回显连接口令。"""
    from campusflow.application.upgrade import UpgradeFailedError

    secret_url = "postgresql+psycopg://campusflow:very-secret-password@127.0.0.1:5432/campusflow"

    def failing(settings: Settings):
        raise UpgradeFailedError(
            f"数据库迁移失败，已自动恢复升级前的数据库与资料备份。备份保留在 /tmp/b。"
            f"（连接 {secret_url}）"
        )

    monkeypatch.setattr(upgrade_cli, "upgrade_if_needed", failing)
    monkeypatch.setenv("CAMPUSFLOW_DATABASE_URL", secret_url)
    assert cli.main(["upgrade"]) == 1
    captured = capsys.readouterr()
    assert "very-secret-password" not in captured.out + captured.err
    assert "***" in captured.err


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__]))
