"""P011 升级迁移、备份与恢复测试。

数据库用例默认使用后端测试库；如设置 ``CAMPUSFLOW_P011_TEST_DATABASE_URL``
则改用专用 scratch 库。用例会把库降到旧 revision 再升级，结束时恢复到 head。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from campusflow.application.upgrade import (
    BackupError,
    DatabaseAheadError,
    UpgradeFailedError,
    UpgradeRequiredError,
    UpgradeState,
    check_schema_or_raise,
    evaluate_upgrade,
    redact_database_url,
    redact_text,
    run_upgrade,
    upgrade_if_needed,
)
from campusflow.core.config import Settings
from campusflow.infrastructure.backup.postgres import (
    create_pre_upgrade_backup,
    locate_pg_tool,
    restore_pre_upgrade_backup,
)
from campusflow.infrastructure.db.migrations import build_alembic_config
from campusflow.infrastructure.db.schema import (
    bundled_head,
    settings_database_revision,
)

P011_DATABASE_URL = os.environ.get("CAMPUSFLOW_P011_TEST_DATABASE_URL") or os.environ.get(
    "CAMPUSFLOW_TEST_DATABASE_URL",
    "postgresql+psycopg://campusflow:campusflow@127.0.0.1:5432/campusflow_test",
)
OLD_REVISION = "n005_web_snapshot_metadata"
FUTURE_REVISION = "zzz_future_revision_2099"


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@pytest.fixture
def upgrade_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    engine = create_engine(P011_DATABASE_URL, connect_args={"connect_timeout": 3})
    try:
        with engine.connect():
            pass
    except Exception as exc:  # 测试库不可达时不伪造通过
        pytest.skip(f"升级测试需要 PostgreSQL：{exc}")
    finally:
        engine.dispose()

    # Alembic env.py 从环境变量读取地址；用例结束后由 monkeypatch 恢复。
    monkeypatch.setenv("CAMPUSFLOW_DATABASE_URL", P011_DATABASE_URL)
    config = build_alembic_config()
    command.upgrade(config, "head")

    storage = tmp_path / "data" / "materials"
    (storage / "2026" / "10").mkdir(parents=True)
    settings = Settings(
        _env_file=None,
        database_url=P011_DATABASE_URL,
        data_dir=str(tmp_path / "data"),
        storage_dir=str(storage),
        backup_retention_count=5,
    )
    try:
        yield settings
    finally:
        # 无论用例成功与否都恢复 head，避免影响后续测试与开发库。
        current = settings_database_revision(settings)
        if current != bundled_head():
            if current is not None and "," not in current:
                command.upgrade(config, "head")
            else:
                _write_version(settings, bundled_head())


def _write_version(settings: Settings, version: str) -> None:
    engine = create_engine(settings.database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num = :version"),
                {"version": version},
            )
    finally:
        engine.dispose()


def _downgrade_to_old(settings: Settings) -> None:
    command.downgrade(build_alembic_config(), OLD_REVISION)


def _insert_workspace(settings: Settings, name: str) -> None:
    engine = create_engine(settings.database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO workspaces (name, timezone) VALUES (:name, 'Asia/Shanghai')"),
                {"name": name},
            )
    finally:
        engine.dispose()


def _workspace_exists(settings: Settings, name: str) -> bool:
    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            count = connection.execute(
                text("SELECT count(*) FROM workspaces WHERE name = :name"), {"name": name}
            ).scalar()
    finally:
        engine.dispose()
    return bool(count)


def _table_exists(settings: Settings, name: str) -> bool:
    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            count = connection.execute(
                text(
                    "SELECT count(*) FROM pg_tables "
                    "WHERE schemaname = current_schema() AND tablename = :name"
                ),
                {"name": name},
            ).scalar()
    finally:
        engine.dispose()
    return bool(count)


def _backup_count(settings: Settings) -> list[Path]:
    root = Path(settings.data_dir or Path(settings.storage_dir).parent) / "backups"
    if not root.is_dir():
        return []
    return sorted(
        path for path in root.iterdir() if path.is_dir() and path.name.startswith("pre-upgrade-")
    )


def test_evaluate_upgrade_matrix() -> None:
    known = {"old", "new", "branch"}
    assert evaluate_upgrade(None, "new") is UpgradeState.UNINITIALIZED
    assert evaluate_upgrade("", "new") is UpgradeState.UNINITIALIZED
    assert evaluate_upgrade("new", "new", known_revisions=known) is UpgradeState.UP_TO_DATE
    assert evaluate_upgrade("old", "new", known_revisions=known) is UpgradeState.UPGRADE_REQUIRED
    assert (
        evaluate_upgrade("old,branch", "new", known_revisions=known)
        is UpgradeState.UPGRADE_REQUIRED
    )
    assert evaluate_upgrade("future", "new", known_revisions=known) is UpgradeState.DATABASE_AHEAD
    # 未提供迁移图时无法证明是祖先，保守按“数据库更新”拒绝。
    assert evaluate_upgrade("old", "new") is UpgradeState.DATABASE_AHEAD


def test_redaction_hides_password_only() -> None:
    url = "postgresql+psycopg://student:s3cr3t-token@127.0.0.1:5432/campusflow"
    redacted = redact_database_url(url)
    assert "s3cr3t-token" not in redacted
    assert "student:***@127.0.0.1:5432/campusflow" in redacted
    assert redact_text("连接失败 s3cr3t-token", url) == "连接失败 ***"


def test_missing_pg_tool_has_actionable_chinese_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "")
    with pytest.raises(BackupError) as exc_info:
        locate_pg_tool("pg_dump", str(tmp_path / "no-pg"))
    message = str(exc_info.value)
    assert "未找到 pg_dump" in message
    assert "CAMPUSFLOW_PG_BIN" in message


def test_backup_contains_dump_storage_and_checksums(upgrade_settings: Settings) -> None:
    settings = upgrade_settings
    storage = Path(settings.storage_dir)
    (storage / "2026" / "10" / "note.txt").write_text("原始资料", encoding="utf-8")
    (storage / "2026" / "10" / "plan.txt").write_text("计划", encoding="utf-8")
    head = bundled_head()
    revision = settings_database_revision(settings)

    info = create_pre_upgrade_backup(settings, db_revision=revision, app_head=head)

    manifest_path = info.path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["database_revision"] == revision
    assert manifest["app_schema_head"] == head
    assert manifest["database"]["dump"] == "database.dump"
    dump_path = info.path / "database.dump"
    assert dump_path.stat().st_size == manifest["database"]["size_bytes"]
    assert _sha256(dump_path) == manifest["database"]["sha256"]
    assert manifest["materials"]["file_count"] == 2
    assert manifest["materials"]["total_bytes"] == len("原始资料".encode()) + len("计划".encode())
    for item in manifest["materials"]["files"]:
        copied = info.path / "materials" / item["path"]
        assert copied.is_file()
        assert _sha256(copied) == item["sha256"]
        assert copied.stat().st_size == item["size_bytes"]

    raw_manifest = manifest_path.read_text(encoding="utf-8")
    assert "campusflow:campusflow@" not in raw_manifest
    assert ":***@" in manifest["database_url"]


def test_upgrade_from_old_revision_backs_up_and_reaches_head(
    upgrade_settings: Settings,
) -> None:
    settings = upgrade_settings
    _downgrade_to_old(settings)
    _insert_workspace(settings, "p011-preserve")
    storage_file = Path(settings.storage_dir) / "2026" / "10" / "keep.txt"
    storage_file.write_text("保留资料", encoding="utf-8")

    report = upgrade_if_needed(settings)

    assert report.action == "upgraded"
    assert report.state is UpgradeState.UP_TO_DATE
    assert report.previous_revision == OLD_REVISION
    assert report.bundled_head == bundled_head()
    assert report.backup is not None
    manifest = json.loads((report.backup.path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["database_revision"] == OLD_REVISION
    assert manifest["app_schema_head"] == bundled_head()
    assert manifest["materials"]["file_count"] == 1
    assert settings_database_revision(settings) == bundled_head()
    assert _workspace_exists(settings, "p011-preserve")
    assert check_schema_or_raise(settings) is UpgradeState.UP_TO_DATE


def test_failed_migration_restores_backup_and_surfaces_error(
    upgrade_settings: Settings,
) -> None:
    settings = upgrade_settings
    _downgrade_to_old(settings)
    _insert_workspace(settings, "p011-preserve-failure")

    def broken_migrate(target_settings: Settings, target: str) -> None:
        engine = create_engine(target_settings.database_url)
        try:
            with engine.begin() as connection:
                connection.execute(text("CREATE TABLE p011_half_upgrade (id integer)"))
                connection.execute(
                    text("INSERT INTO workspaces (name, timezone) VALUES ('p011-half', 'UTC')")
                )
                connection.execute(text("UPDATE alembic_version SET version_num = 'p011_half'"))
        finally:
            engine.dispose()
        raise RuntimeError("模拟迁移中断")

    with pytest.raises(UpgradeFailedError) as exc_info:
        run_upgrade(settings, migrate=broken_migrate)

    assert "已自动恢复升级前的数据库与资料备份" in str(exc_info.value)
    assert settings_database_revision(settings) == OLD_REVISION
    assert _workspace_exists(settings, "p011-preserve-failure")
    assert not _workspace_exists(settings, "p011-half")
    assert not _table_exists(settings, "p011_half_upgrade")
    backups = _backup_count(settings)
    assert len(backups) == 1
    assert (backups[0] / "manifest.json").is_file()


def test_corrupted_backup_is_rejected_before_touching_database(
    upgrade_settings: Settings,
) -> None:
    settings = upgrade_settings
    head = bundled_head()
    info = create_pre_upgrade_backup(
        settings, db_revision=settings_database_revision(settings), app_head=head
    )
    (info.path / "database.dump").write_bytes(b"corrupted")

    with pytest.raises(BackupError) as exc_info:
        restore_pre_upgrade_backup(settings, info)

    assert "校验失败" in str(exc_info.value)
    assert settings_database_revision(settings) == head


def test_retention_keeps_newest_n_after_successful_upgrade(
    upgrade_settings: Settings,
) -> None:
    settings = upgrade_settings
    root = Path(settings.data_dir or "") / "backups"
    root.mkdir(parents=True, exist_ok=True)
    for stamp in ("20200101T000000Z", "20200102T000000Z", "20200103T000000Z"):
        old = root / f"pre-upgrade-{stamp}"
        old.mkdir()
        (old / "manifest.json").write_text("{}", encoding="utf-8")
    _downgrade_to_old(settings)
    settings.backup_retention_count = 2

    report = upgrade_if_needed(settings)

    remaining = [path.name for path in _backup_count(settings)]
    assert report.backup is not None
    assert report.backup.path.name in remaining
    assert "pre-upgrade-20200103T000000Z" in remaining
    assert "pre-upgrade-20200101T000000Z" not in remaining
    assert "pre-upgrade-20200102T000000Z" not in remaining
    assert len(remaining) == 2
    assert set(report.pruned_backups) == {
        "pre-upgrade-20200101T000000Z",
        "pre-upgrade-20200102T000000Z",
    }


def test_retention_is_not_applied_after_failed_upgrade(upgrade_settings: Settings) -> None:
    settings = upgrade_settings
    root = Path(settings.data_dir or "") / "backups"
    root.mkdir(parents=True, exist_ok=True)
    for stamp in ("20200101T000000Z", "20200102T000000Z", "20200103T000000Z"):
        old = root / f"pre-upgrade-{stamp}"
        old.mkdir()
        (old / "manifest.json").write_text("{}", encoding="utf-8")
    _downgrade_to_old(settings)
    settings.backup_retention_count = 1

    def broken_migrate(target_settings: Settings, target: str) -> None:
        raise RuntimeError("模拟迁移中断")

    with pytest.raises(UpgradeFailedError):
        run_upgrade(settings, migrate=broken_migrate)

    remaining = [path.name for path in _backup_count(settings)]
    assert "pre-upgrade-20200101T000000Z" in remaining
    assert "pre-upgrade-20200102T000000Z" in remaining
    assert "pre-upgrade-20200103T000000Z" in remaining


def test_database_ahead_refuses_without_backup_or_migration(
    upgrade_settings: Settings,
) -> None:
    settings = upgrade_settings
    _write_version(settings, FUTURE_REVISION)
    calls: list[str] = []

    def unexpected_backup(*args: object, **kwargs: object) -> None:
        calls.append("backup")

    def unexpected_migrate(*args: object) -> None:
        calls.append("migrate")

    try:
        with pytest.raises(DatabaseAheadError) as exc_info:
            run_upgrade(settings, backup=unexpected_backup, migrate=unexpected_migrate)
        assert "高于当前应用内置版本" in str(exc_info.value)
        assert calls == []
        with pytest.raises(DatabaseAheadError):
            check_schema_or_raise(settings)
    finally:
        _write_version(settings, bundled_head())
    assert settings_database_revision(settings) == bundled_head()


def test_gate_requires_upgrade_when_database_is_old(upgrade_settings: Settings) -> None:
    settings = upgrade_settings
    _downgrade_to_old(settings)
    with pytest.raises(UpgradeRequiredError) as exc_info:
        check_schema_or_raise(settings)
    assert "需要升级" in str(exc_info.value)


def test_backup_failure_aborts_before_migration(upgrade_settings: Settings) -> None:
    settings = upgrade_settings
    _downgrade_to_old(settings)
    calls: list[str] = []

    def failing_backup(*args: object, **kwargs: object) -> None:
        raise BackupError("升级前备份失败")

    def unexpected_migrate(*args: object) -> None:
        calls.append("migrate")

    with pytest.raises(BackupError):
        run_upgrade(settings, backup=failing_backup, migrate=unexpected_migrate)

    assert calls == []
    assert settings_database_revision(settings) == OLD_REVISION


def test_uninitialized_database_skips_backup_and_creates_schema(
    upgrade_settings: Settings,
) -> None:
    settings = upgrade_settings
    target = bundled_head()
    state = {"current": None}

    def fake_revision(target_settings: Settings) -> str | None:
        return state["current"]

    def fake_migrate(target_settings: Settings, revision: str) -> None:
        state["current"] = revision

    def unexpected_backup(*args: object, **kwargs: object) -> None:
        raise AssertionError("空库没有可备份数据，不应创建升级前备份")

    report = run_upgrade(
        settings,
        backup=unexpected_backup,
        migrate=fake_migrate,
        revision=fake_revision,
        head=lambda _: target,
        known_revisions={target},
    )

    assert report.action == "created_schema"
    assert report.previous_revision is None
    assert report.backup is None
    assert state["current"] == target


def test_gate_refuses_uninitialized_database(upgrade_settings: Settings) -> None:
    settings = upgrade_settings
    with pytest.raises(UpgradeRequiredError) as exc_info:
        check_schema_or_raise(
            settings,
            head=lambda _: "new",
            revision=lambda _: None,
            known_revisions={"new"},
        )
    assert "尚未初始化" in str(exc_info.value)
