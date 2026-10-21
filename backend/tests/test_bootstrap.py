"""P007：桌面模式首启初始化、凭据存储与恢复行为。

这些用例会真正执行 initdb、启动短生命周期的 PostgreSQL 并跑迁移；本机缺少
`initdb`／`pg_ctl`／`pg_isready` 时整体跳过。每个用例使用独立的短路径临时目录，
结束时停止集群并断言没有残留 postgres 进程。
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from alembic.script import ScriptDirectory

from campusflow.application import bootstrap
from campusflow.application.bootstrap import (
    DB_PASSWORD_ACCOUNT,
    LOCAL_PASSWORD_ACCOUNT,
    BootstrapError,
    BootstrapStatus,
    PgConnection,
    connection_env,
    ensure_ready,
    initialize,
    inspect,
)
from campusflow.application.ports.secrets import Secret
from campusflow.core.config import Settings
from campusflow.infrastructure.db.migrations import build_runtime_alembic_config
from campusflow.infrastructure.db.postgres_cluster import PgBinaries
from campusflow.infrastructure.secrets import FileSecretStore
from campusflow.infrastructure.secrets.keychain import KeychainSecretStore
from campusflow.infrastructure.secrets.memory import InMemorySecretStore

PG_AVAILABLE = PgBinaries.available()
pytestmark = pytest.mark.skipif(
    not PG_AVAILABLE,
    reason="本机缺少 initdb／pg_ctl／pg_isready，跳过桌面首启集成测试",
)

_TIMEOUT_SECONDS = 30


def _alembic_head() -> str:
    head = ScriptDirectory.from_config(build_runtime_alembic_config()).get_current_head()
    assert head is not None
    return head


def _short_temp_base() -> Path:
    base = Path("/tmp") if Path("/tmp").is_dir() else Path(tempfile.gettempdir())
    return Path(tempfile.mkdtemp(prefix="cfs-p007-", dir=base))


def _postgres_pids(pgdata: Path) -> list[str]:
    if sys.platform == "win32":
        return []
    result = subprocess.run(
        ["pgrep", "-f", str(pgdata)], capture_output=True, text=True, check=False
    )
    return [line for line in result.stdout.split() if line]


def _stop_cluster(pgdata: Path) -> None:
    if not (pgdata / "PG_VERSION").is_file():
        return
    binaries = PgBinaries.discover()
    subprocess.run(
        [
            str(binaries.tool("pg_ctl")),
            "-D",
            str(pgdata),
            "-m",
            "fast",
            "-w",
            "-t",
            "20",
            "stop",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=_TIMEOUT_SECONDS,
    )


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture
def work_root() -> Iterator[Path]:
    base = _short_temp_base()
    root = base / "data"
    try:
        yield root
    finally:
        pgdata = root / "pgdata"
        _stop_cluster(pgdata)
        leftovers = _postgres_pids(pgdata)
        shutil.rmtree(base, ignore_errors=True)
        assert not leftovers, f"测试数据目录仍有 PostgreSQL 残留进程：{leftovers}"


def _settings(root: Path, **overrides) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "deployment_mode": "desktop",
        "data_dir": str(root),
        "secret_backend": "file",
        "recovery_interval_seconds": 0,
    }
    values.update(overrides)
    return Settings(**values)


def _read_state(root: Path) -> dict:
    return json.loads((root / "state.json").read_text(encoding="utf-8"))


def _read_secret_file(root: Path) -> dict[str, str]:
    return json.loads((root / "secrets.json").read_text(encoding="utf-8"))


def _log_text(root: Path) -> str:
    chunks = []
    for path in (root / "logs").glob("*"):
        if path.is_file():
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def test_fresh_initialize_creates_layout_and_state(work_root: Path) -> None:
    settings = _settings(work_root)
    report = initialize(settings)

    assert report.status is BootstrapStatus.INITIALIZED
    assert report.initialized is True
    assert report.ready is False  # 初始化结束时集群按契约停止
    assert report.postgres_running is False

    for name in ("pgdata", "materials", "logs", "backups", "run"):
        assert (work_root / name).is_dir(), name
    assert (work_root / "pgdata" / "PG_VERSION").is_file()
    if os.name != "nt":
        assert (work_root / "pgdata").stat().st_uid == os.getuid()
        assert stat.S_IMODE(work_root.stat().st_mode) == 0o700

    state = _read_state(work_root)
    assert set(state) == {
        "format_version",
        "created_at",
        "pg_version",
        "port",
        "database",
        "username",
        "schema_head",
        "secret_backend",
    }
    assert state["format_version"] == bootstrap.STATE_FORMAT_VERSION
    assert state["database"] == "campusflow"
    assert state["username"] == "campusflow"
    assert state["secret_backend"] == "file"
    assert state["schema_head"] == _alembic_head()
    assert isinstance(state["port"], int) and 1024 < state["port"] <= 65535
    assert state["pg_version"].count(".") >= 1
    assert state["created_at"].endswith("Z")

    assert report.connection is not None
    db_password = report.connection.password.reveal()
    assert report.local_password is not None
    local_password = report.local_password.reveal()
    assert len(db_password) >= 32
    assert len(local_password) >= 32
    assert db_password != local_password
    assert db_password != "campusflow-dev"
    assert local_password != "campusflow-dev"
    assert report.local_username != "student"
    assert state["port"] == report.connection.port

    state_text = (work_root / "state.json").read_text(encoding="utf-8")
    assert db_password not in state_text
    assert local_password not in state_text
    log_text = _log_text(work_root)
    assert db_password not in log_text
    assert local_password not in log_text

    stored = _read_secret_file(work_root)
    assert stored[DB_PASSWORD_ACCOUNT] == db_password
    assert stored[LOCAL_PASSWORD_ACCOUNT] == local_password
    if os.name != "nt":
        assert stat.S_IMODE((work_root / "secrets.json").stat().st_mode) == 0o600


def test_ensure_ready_starts_cluster_with_extension_and_schema(work_root: Path) -> None:
    settings = _settings(work_root)
    initialized = initialize(settings)
    assert initialized.connection is not None
    assert _port_open(initialized.connection.port) is False

    report = ensure_ready(settings)
    assert report.ready is True
    assert report.started_postgres is True
    assert report.postgres_running is True
    assert report.connection is not None
    assert report.local_password is not None
    assert report.local_password.reveal() == initialized.local_password.reveal()

    connection = report.connection
    with psycopg.connect(
        host=connection.host,
        port=connection.port,
        dbname=connection.database,
        user=connection.username,
        password=connection.password.reveal(),
        connect_timeout=10,
    ) as live:
        extension = live.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
        head = live.execute("SELECT version_num FROM alembic_version").fetchone()
        port = live.execute("SHOW port").fetchone()
        listen = live.execute("SHOW listen_addresses").fetchone()
        tables = live.execute(
            "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'"
        ).fetchone()
    assert extension is not None and extension[0]
    assert head is not None and head[0] == initialized.schema_head
    assert port is not None and port[0] == str(connection.port)
    assert listen is not None and listen[0] == "127.0.0.1"
    assert tables is not None and tables[0] > 0

    binaries = PgBinaries.discover()
    ready = subprocess.run(
        [str(binaries.tool("pg_isready")), "-h", "127.0.0.1", "-p", str(connection.port), "-q"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ready.returncode == 0, ready.stdout + ready.stderr

    again = ensure_ready(settings)
    assert again.ready is True
    assert again.started_postgres is False

    environment = connection_env(again)
    assert environment["CAMPUSFLOW_DATABASE_URL"].startswith("postgresql+psycopg://campusflow:")
    assert environment["CAMPUSFLOW_LOCAL_PASSWORD"] == again.local_password.reveal()
    for text in (repr(initialized), repr(report), repr(again)):
        assert connection.password.reveal() not in text
        assert again.local_password.reveal() not in text


def test_second_initialize_is_idempotent(work_root: Path) -> None:
    settings = _settings(work_root)
    first = initialize(settings)
    marker = work_root / "materials" / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    state_before = (work_root / "state.json").read_text(encoding="utf-8")

    second = initialize(settings)

    assert second.status is BootstrapStatus.INITIALIZED
    assert second.initialized is False
    assert second.ready is False
    assert second.connection is None
    assert second.schema_head == first.schema_head
    assert (work_root / "state.json").read_text(encoding="utf-8") == state_before
    assert marker.read_text(encoding="utf-8") == "keep"
    assert not list(work_root.parent.glob("data.init-*"))


def test_migration_failure_leaves_no_usable_data_dir(
    work_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(work_root)

    def fail_migration(*args: object, **kwargs: object) -> str:
        raise RuntimeError("迁移注入失败")

    monkeypatch.setattr(bootstrap, "upgrade_to_head", fail_migration)

    with pytest.raises(BootstrapError) as excinfo:
        initialize(settings)

    assert not work_root.exists()
    assert not (work_root / "state.json").exists()
    assert not list(work_root.parent.glob("data.init-*"))
    failed = sorted(work_root.parent.glob("data.failed-*"))
    assert len(failed) == 1
    logs = failed[0] / "logs"
    assert logs.is_dir()
    assert list(logs.iterdir())
    assert excinfo.value.log_dir == logs
    assert "迁移" in str(excinfo.value) or "RuntimeError" in str(excinfo.value)
    assert not _postgres_pids(work_root / "pgdata")


@pytest.mark.skipif(os.name == "nt", reason="用 POSIX 脚本模拟损坏的 initdb")
def test_broken_pg_binaries_are_reported(work_root: Path, tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("initdb", "pg_ctl", "pg_isready"):
        script = bindir / name
        script.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        script.chmod(0o755)

    settings = _settings(work_root, pg_bin=str(bindir))
    with pytest.raises(BootstrapError, match="initdb"):
        initialize(settings)
    assert not work_root.exists()


def test_initialize_refuses_partially_initialized_dir(tmp_path: Path) -> None:
    root = tmp_path / "data"
    (root / "pgdata").mkdir(parents=True)
    (root / "pgdata" / "PG_VERSION").write_text("16\n", encoding="utf-8")
    settings = _settings(root)

    view = inspect(settings)
    assert view.status is BootstrapStatus.PARTIALLY_INITIALIZED
    with pytest.raises(BootstrapError, match="拒绝覆盖"):
        initialize(settings)
    assert not (root / "state.json").exists()


def test_incompatible_state_format_refuses(tmp_path: Path) -> None:
    root = tmp_path / "data"
    (root / "pgdata").mkdir(parents=True)
    (root / "pgdata" / "PG_VERSION").write_text("16\n", encoding="utf-8")
    state = {
        "format_version": bootstrap.STATE_FORMAT_VERSION + 1,
        "created_at": "2030-01-01T00:00:00Z",
        "pg_version": "17.0",
        "port": 5433,
        "database": "campusflow",
        "username": "campusflow",
        "schema_head": "head_from_newer_app",
        "secret_backend": "macos-keychain",
    }
    (root / "state.json").write_text(json.dumps(state), encoding="utf-8")
    settings = _settings(root)

    view = inspect(settings)
    assert view.status is BootstrapStatus.INCOMPATIBLE
    assert view.reasons
    with pytest.raises(BootstrapError, match="拒绝覆盖"):
        initialize(settings)
    assert _read_state(root)["format_version"] == bootstrap.STATE_FORMAT_VERSION + 1


def test_missing_database_password_is_reported_without_defaults(work_root: Path) -> None:
    settings = _settings(work_root)
    initialized = initialize(settings)
    assert initialized.connection is not None
    port = initialized.connection.port

    secrets_path = work_root / "secrets.json"
    stored = _read_secret_file(work_root)
    stored.pop(DB_PASSWORD_ACCOUNT)
    secrets_path.write_text(json.dumps(stored), encoding="utf-8")

    view = inspect(settings)
    assert view.status is BootstrapStatus.PARTIALLY_INITIALIZED
    assert any("凭据丢失" in reason for reason in view.reasons)

    report = ensure_ready(settings)
    assert report.ready is False
    assert report.status is BootstrapStatus.PARTIALLY_INITIALIZED
    assert any("凭据丢失" in message for message in report.messages)
    assert any("默认口令" in message for message in report.messages)
    assert report.postgres_running is False
    assert _port_open(port) is False


def test_missing_local_password_is_regenerated(work_root: Path) -> None:
    settings = _settings(work_root)
    initialized = initialize(settings)
    assert initialized.local_password is not None
    previous = initialized.local_password.reveal()

    secrets_path = work_root / "secrets.json"
    stored = _read_secret_file(work_root)
    stored.pop(LOCAL_PASSWORD_ACCOUNT)
    secrets_path.write_text(json.dumps(stored), encoding="utf-8")

    report = ensure_ready(settings)

    assert report.ready is True
    assert report.local_password is not None
    regenerated = report.local_password.reveal()
    assert regenerated != previous
    assert regenerated != "campusflow-dev"
    assert _read_secret_file(work_root)[LOCAL_PASSWORD_ACCOUNT] == regenerated
    assert any("重新生成" in warning for warning in report.warnings)


def test_secret_store_injection_avoids_plaintext_file(work_root: Path) -> None:
    store = InMemorySecretStore()
    settings = _settings(work_root)

    report = initialize(settings, secret_store=store)

    assert report.secret_backend == "memory"
    assert not (work_root / "secrets.json").exists()
    assert report.connection is not None
    db_password = report.connection.password.reveal()
    assert store.get(DB_PASSWORD_ACCOUNT) == db_password
    assert store.get(LOCAL_PASSWORD_ACCOUNT) == report.local_password.reveal()
    assert db_password not in (work_root / "state.json").read_text(encoding="utf-8")
    assert db_password not in _log_text(work_root)

    ready = ensure_ready(settings, secret_store=store)
    assert ready.ready is True
    assert ready.local_password is not None
    assert ready.local_password.reveal() == report.local_password.reveal()


def test_desktop_gate_rejects_server_mode_and_relative_data_dir(tmp_path: Path) -> None:
    server = Settings(_env_file=None)
    with pytest.raises(BootstrapError, match="桌面模式"):
        inspect(server)
    with pytest.raises(BootstrapError, match="桌面模式"):
        initialize(server)

    relative = Settings.model_construct(
        deployment_mode="desktop",
        data_dir="relative/data",
        secret_backend="auto",
        pg_bin=None,
        migrations_dir=None,
    )
    with pytest.raises(BootstrapError, match="绝对路径"):
        inspect(relative)
    with pytest.raises(BootstrapError, match="绝对路径"):
        ensure_ready(relative)


def test_uninitialized_ensure_ready_asks_for_initialize(work_root: Path) -> None:
    report = ensure_ready(_settings(work_root))
    assert report.status is BootstrapStatus.UNINITIALIZED
    assert report.ready is False
    assert any("尚未初始化" in message for message in report.messages)
    assert not work_root.exists()


def test_secret_wrapper_and_file_store_roundtrip(tmp_path: Path) -> None:
    secret = Secret("s3cret-value")
    assert secret.reveal() == "s3cret-value"
    assert str(secret) == "***"
    assert repr(secret) == "Secret(***)"
    assert secret == Secret("s3cret-value")
    assert secret != Secret("other")
    with pytest.raises(ValueError):
        Secret("")

    connection = PgConnection(
        host="127.0.0.1", port=5432, database="campusflow", username="campusflow", password=secret
    )
    assert "s3cret-value" not in repr(connection)
    assert "s3cret-value" not in str(connection)

    store = FileSecretStore(tmp_path / "secrets.json")
    assert store.backend == "file"
    assert store.get(DB_PASSWORD_ACCOUNT) is None
    store.set(DB_PASSWORD_ACCOUNT, "value-1")
    store.set(LOCAL_PASSWORD_ACCOUNT, "value-2")
    assert store.get(DB_PASSWORD_ACCOUNT) == "value-1"
    assert store.get(LOCAL_PASSWORD_ACCOUNT) == "value-2"
    assert store.delete(DB_PASSWORD_ACCOUNT) is True
    assert store.delete(DB_PASSWORD_ACCOUNT) is False
    assert store.get(DB_PASSWORD_ACCOUNT) is None
    if os.name != "nt":
        mode = stat.S_IMODE((tmp_path / "secrets.json").stat().st_mode)
        assert mode == 0o600


@pytest.mark.skipif(
    not KeychainSecretStore.is_available(), reason="本机没有可用的 macOS 钥匙串"
)
def test_macos_keychain_roundtrip() -> None:
    if os.environ.get("CAMPUSFLOW_TEST_KEYCHAIN") != "1":
        pytest.skip("钥匙串往返会写入登录钥匙串，设置 CAMPUSFLOW_TEST_KEYCHAIN=1 才执行")
    store = KeychainSecretStore(service="campusflow-p007-test")
    account = f"roundtrip-{secrets.token_hex(4)}"
    try:
        store.set(account, "value-1")
        assert store.get(account) == "value-1"
        store.set(account, "value-2")
        assert store.get(account) == "value-2"
    finally:
        assert store.delete(account) is True
        assert store.get(account) is None


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI 只在 Windows 上可用")
def test_windows_dpapi_roundtrip(tmp_path: Path) -> None:
    from campusflow.infrastructure.secrets.dpapi import WindowsDpapiSecretStore

    store = WindowsDpapiSecretStore(tmp_path / "secrets.dpapi")
    store.set(DB_PASSWORD_ACCOUNT, "value-1")
    assert store.get(DB_PASSWORD_ACCOUNT) == "value-1"
    # 密文文件里不出现明文
    assert "value-1" not in (tmp_path / "secrets.dpapi").read_text(encoding="utf-8")
    assert store.delete(DB_PASSWORD_ACCOUNT) is True
