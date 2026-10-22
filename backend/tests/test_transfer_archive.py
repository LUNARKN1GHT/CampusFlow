"""P010：`.campusflow` 全量导出与导入验收。

覆盖：同机导出/导入的条目数、资料文件与校验和一致；路径可移植（相对 POSIX、
中文与空格）；损坏、缺清单、校验和不符、格式版本过新与数据库版本过新被拒绝；
归档不含数据库口令、OAuth 令牌与模型密钥；导入必须带确认令牌且非空目标需
显式 replace；中途失败时本机数据不变、临时库被清理。

数据库：使用真实 PostgreSQL。建库需要 CREATEDB／超级用户，因此测试优先用
`CAMPUSFLOW_TEST_DATABASE_URL` 的账户建库，失败时再尝试 OS 用户（本地 trust
认证）；两者都不可用时整体跳过，而不是悄悄降级为假实现。
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import secrets
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from campusflow.application.transfer import (
    ArchiveFormatError,
    ArchiveVersionError,
    ConfirmationMismatchError,
    ConfirmationRequiredError,
    ImportVerificationError,
    TargetNotEmptyError,
    TransferError,
)
from campusflow.core.config import Settings
from campusflow.infrastructure.db import models
from campusflow.infrastructure.db.migrations import build_alembic_config
from campusflow.infrastructure.transfer import (
    AlembicMigrator,
    build_transfer_service,
    export_workspace,
    import_archive,
    inspect_archive,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent
TEST_DATABASE_URL = os.environ.get(
    "CAMPUSFLOW_TEST_DATABASE_URL",
    "postgresql+psycopg://campusflow:campusflow@127.0.0.1:5432/campusflow_test",
)
FAKE_OAUTH_TOKEN = "ya29.p010-fake-oauth-token-must-not-leak"
FAKE_API_KEY = "sk-p010-fake-model-key-must-not-leak"
FAKE_LOGIN_PASSWORD = "p010-login-password-must-not-leak"

MATERIALS: dict[str, bytes] = {
    "2026/09/课程 资料（一）.pdf": b"%PDF-1.4 P010 round-trip material\n" * 4,
    "2026/10/lecture notes 01.txt": "课堂笔记：10 月 30 日交作业".encode(),
}


# --------------------------------------------------------------------------- 环境


def _engine(url: str, *, autocommit: bool = False):
    return create_engine(
        make_url(url).set(drivername="postgresql+psycopg").render_as_string(hide_password=False),
        poolclass=NullPool,
        isolation_level="AUTOCOMMIT" if autocommit else "SERIALIZABLE",
        connect_args={"connect_timeout": 3},
    )


def _admin_candidates() -> Iterator[str]:
    base = make_url(TEST_DATABASE_URL)
    yield base.set(database="postgres").render_as_string(hide_password=False)
    users = {getpass.getuser(), os.environ.get("USER", "")}
    for user in users:
        if user:
            yield (
                base.set(username=user, password=None, database="postgres").render_as_string(
                    hide_password=False
                )
            )


def _execute_admin(url: str, statement: str, *, ignore_error: bool = False) -> None:
    engine = _engine(url, autocommit=True)
    try:
        with engine.connect() as connection:
            connection.execute(text(statement))
    except Exception:
        if not ignore_error:
            raise
    finally:
        engine.dispose()


def _drop_database(admin_url: str, name: str) -> None:
    engine = _engine(admin_url, autocommit=True)
    try:
        with engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    finally:
        engine.dispose()


def _databases_owned_by(admin_url: str, role: str) -> list[str]:
    engine = _engine(admin_url, autocommit=True)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT datname FROM pg_database WHERE pg_get_userbyid(datdba) = :role"),
                {"role": role},
            )
            return [row[0] for row in rows]
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def admin_url() -> str:
    """返回一个确实能建库/建角色的维护连接；否则跳过整个模块。"""
    for candidate in _admin_candidates():
        probe = f"p010_probe_{secrets.token_hex(4)}"
        try:
            _execute_admin(candidate, f'CREATE DATABASE "{probe}"')
        except Exception:
            continue
        _execute_admin(candidate, f'DROP DATABASE IF EXISTS "{probe}"', ignore_error=True)
        return candidate
    pytest.skip("需要具备 CREATEDB 权限的 PostgreSQL 账户才能验收迁移包")


@pytest.fixture(scope="session")
def transfer_role(admin_url: str) -> Iterator[tuple[str, str]]:
    """独立角色 + 口令：口令与角色名不同，才能验证"口令没有进入归档"。"""
    role = f"p010_role_{secrets.token_hex(4)}"
    password = f"P010-Pass-{secrets.token_hex(8)}"
    _execute_admin(admin_url, f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}'")
    try:
        yield role, password
    finally:
        for database in _databases_owned_by(admin_url, role):
            if database == migrated_template:  # 会话级模板库留给后续用例复制
                continue
            _drop_database(admin_url, database)
        _execute_admin(admin_url, f'DROP ROLE IF EXISTS "{role}"', ignore_error=True)


@contextmanager
def _database_url_env(url: str) -> Iterator[None]:
    previous = os.environ.get("CAMPUSFLOW_DATABASE_URL")
    os.environ["CAMPUSFLOW_DATABASE_URL"] = url
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("CAMPUSFLOW_DATABASE_URL", None)
        else:
            os.environ["CAMPUSFLOW_DATABASE_URL"] = previous


@dataclass
class Site:
    """一次验收用到的源库、目标库与目录。"""

    admin_url: str
    role: str
    password: str
    template: str
    source_name: str
    target_name: str
    database_url: str
    storage: Path
    backup: Path
    target_storage: Path
    archive_dir: Path
    material_ids: dict[str, int]

    @property
    def target_url(self) -> str:
        base = make_url(self.database_url)
        return base.set(database=self.target_name).render_as_string(hide_password=False)

    def settings(self) -> Settings:
        return Settings(
            _env_file=None,
            database_url=self.database_url,
            storage_dir=str(self.storage),
            backup_dir=str(self.backup),
            pg_admin_url=self.admin_url,
        )

    def target_settings(self) -> Settings:
        return Settings(
            _env_file=None,
            database_url=self.target_url,
            storage_dir=str(self.target_storage),
            backup_dir=str(self.backup),
            pg_admin_url=self.admin_url,
        )

    @property
    def archive(self) -> Path:
        return self.archive_dir / "workspace.campusflow"

    @property
    def template_url(self) -> str:
        return (
            make_url(self.database_url)
            .set(database=self.template)
            .render_as_string(hide_password=False)
        )


def _host_port() -> str:
    base = make_url(TEST_DATABASE_URL)
    return f"{base.host or '127.0.0.1'}:{base.port or 5432}"


def _create_database(
    admin_url: str,
    name: str,
    role: str,
    *,
    template: str | None = None,
    with_vector: bool = True,
) -> None:
    """建库并把 OWNER 设为业务角色；扩展由维护账户预创建（业务角色不是超级用户）。

    传入已迁移的空库作为模板可跳过几十个迁移脚本，验收时间才可控。
    """
    source = f'"{template}"' if template else "template0"
    _execute_admin(admin_url, f'CREATE DATABASE "{name}" OWNER "{role}" TEMPLATE {source}')
    if with_vector:
        extension_url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
        _execute_admin(extension_url, "CREATE EXTENSION IF NOT EXISTS vector")


def _migrate(url: str) -> None:
    with _database_url_env(url):
        command.upgrade(build_alembic_config(str(BACKEND_DIR / "migrations")), "head")


@pytest.fixture(scope="session")
def migrated_template(admin_url: str, transfer_role: tuple[str, str]) -> str:
    """迁移到 head 的空模板库：每个用例复制它，避免重复执行整套迁移。"""
    role, _ = transfer_role
    name = f"p010_template_{secrets.token_hex(4)}"
    _create_database(admin_url, name, role)
    host, port = _host_port().split(":")
    _, password = transfer_role
    _migrate(f"postgresql+psycopg://{role}:{password}@{host}:{port}/{name}")
    return name


@pytest.fixture
def site(
    tmp_path: Path,
    admin_url: str,
    transfer_role: tuple[str, str],
    migrated_template: str,
) -> Iterator[Site]:
    role, password = transfer_role
    token = secrets.token_hex(4)
    host, port = _host_port().split(":")
    source_name = f"p010_{token}_src"
    target_name = f"p010_{token}_dst"
    database_url = f"postgresql+psycopg://{role}:{password}@{host}:{port}/{source_name}"
    storage = tmp_path / "materials"
    storage.mkdir()
    backup = tmp_path / "backups"
    archive_dir = tmp_path / "archives"
    archive_dir.mkdir()
    _create_database(admin_url, source_name, role, template=migrated_template)
    try:
        material_ids = _populate(database_url, storage)
        yield Site(
            admin_url=admin_url,
            role=role,
            password=password,
            template=migrated_template,
            source_name=source_name,
            target_name=target_name,
            database_url=database_url,
            storage=storage,
            backup=backup,
            target_storage=tmp_path / "restored-materials",
            archive_dir=archive_dir,
            material_ids=material_ids,
        )
    finally:
        for database in _databases_owned_by(admin_url, role):
            if database == migrated_template:  # 会话级模板库留给后续用例复制
                continue
            _drop_database(admin_url, database)


def _populate(database_url: str, storage: Path) -> dict[str, int]:
    """写入带资料、片段、候选、任务、计划与外部日历连接的完整样本。"""
    engine = create_engine(database_url)
    material_ids: dict[str, int] = {}
    try:
        with Session(engine) as session:
            workspace = models.Workspace(name="迁移测试空间", timezone="Asia/Shanghai")
            session.add(workspace)
            session.flush()
            semester = models.Semester(
                workspace_id=workspace.id,
                name="2026 秋",
                start_date=date(2026, 9, 1),
                end_date=date(2027, 1, 15),
            )
            session.add(semester)
            session.flush()
            course = models.Course(
                workspace_id=workspace.id,
                semester_id=semester.id,
                name="数据结构",
                code="CS201",
                teacher="张老师",
            )
            session.add(course)
            session.flush()

            first_version_id = None
            first_chunk = None
            for index, (relative, content) in enumerate(sorted(MATERIALS.items()), start=1):
                path = storage / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                material = models.Material(
                    workspace_id=workspace.id,
                    semester_id=semester.id,
                    course_id=course.id,
                    title=f"资料 {index}",
                    source_type="pdf",
                    status="ready",
                )
                session.add(material)
                session.flush()
                version = models.MaterialVersion(
                    material_id=material.id,
                    version_no=1,
                    storage_key=relative,
                    checksum=hashlib.sha256(content).hexdigest(),
                )
                session.add(version)
                session.flush()
                material_ids[relative] = material.id
                if first_version_id is None:
                    first_version_id = version.id
                    first_chunk = models.SourceChunk(
                        version_id=version.id,
                        seq=0,
                        locator_type="page",
                        locator_value="第 1 页",
                        page=1,
                        text="作业 10 月 30 日 23:59 前提交",
                    )
                    session.add(first_chunk)
                    session.flush()

            assert first_version_id is not None and first_chunk is not None
            candidate = models.ExtractedItem(
                workspace_id=workspace.id,
                source_version_id=first_version_id,
                kind="task",
                review_status="pending",
                semester_id=semester.id,
                course_id=course.id,
                review_reasons=["date_incomplete"],
            )
            session.add(candidate)
            session.flush()
            session.add(
                models.ExtractedField(
                    candidate_id=candidate.id,
                    name="title",
                    value="第一次作业",
                    origin="document",
                )
            )
            task = models.Task(
                workspace_id=workspace.id,
                course_id=course.id,
                title="第一次作业",
                due_date=date(2026, 10, 30),
                priority="medium",
                progress="not_started",
                estimated_minutes=120,
                remaining_minutes=120,
            )
            session.add(task)
            session.flush()
            draft = models.PlanDraft(
                workspace_id=workspace.id,
                semester_id=semester.id,
                range_start=date(2026, 10, 20),
                range_end=date(2026, 11, 2),
                status="accepted",
                input_snapshot={"source": "manual"},
                input_fingerprint="f" * 64,
                accepted_at=datetime(2026, 10, 20, tzinfo=UTC),
            )
            session.add(draft)
            session.flush()
            session.add(
                models.PlanBlock(
                    draft_id=draft.id,
                    task_id=task.id,
                    title="第一次作业",
                    starts_at=datetime(2026, 10, 21, 10, tzinfo=UTC),
                    ends_at=datetime(2026, 10, 21, 12, tzinfo=UTC),
                    minutes=120,
                    reason="先满足截止时间",
                )
            )
            profile = models.EmbeddingProfileRecord(
                id="zp:embedding-3:q001-v1:1024",
                provider="zhipu",
                model="embedding-3",
                revision="q001-v1",
                dimensions=1024,
                generation_config={"dimensions": 1024},
            )
            session.add(profile)
            session.flush()
            session.add(
                models.IndexedBlock(
                    profile_id=profile.id,
                    block_id="blk-1",
                    workspace_id=workspace.id,
                    version_id=first_version_id,
                    source_chunk_id=first_chunk.id,
                    source_fingerprint="a" * 64,
                    start_offset=0,
                    end_offset=10,
                    chunker_version="q003-v1",
                    text="作业 10 月 30 日",
                    state="done",
                    embedding=[0.01] * 1024,
                )
            )
            session.add(
                models.CalendarConnection(
                    workspace_id=workspace.id,
                    provider="google",
                    account_email="student@example.com",
                    calendar_id="primary",
                    access_token_ciphertext=FAKE_OAUTH_TOKEN,
                )
            )
            session.commit()
    finally:
        engine.dispose()
    return material_ids


def _counts(url: str) -> dict[str, int]:
    from campusflow.application.transfer.manifest import COUNT_TABLES

    engine = create_engine(url)
    counts: dict[str, int] = {}
    try:
        with engine.connect() as connection:
            for key, table in COUNT_TABLES.items():
                counts[key] = connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
    finally:
        engine.dispose()
    return counts


def _database_exists(admin_url: str, name: str) -> bool:
    engine = _engine(admin_url, autocommit=True)
    try:
        with engine.connect() as connection:
            return bool(
                connection.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
                ).scalar()
            )
    finally:
        engine.dispose()


def _sentinel_target(site: Site) -> None:
    """建一个已迁移、只含一条"旧数据"的目标库，用来证明失败时原数据不受影响。"""
    _create_database(site.admin_url, site.target_name, site.role, template=site.template)
    engine = create_engine(site.target_url)
    try:
        with Session(engine) as session:
            session.add(models.Workspace(name="旧数据"))
            session.commit()
    finally:
        engine.dispose()


def _sentinel_names(site: Site) -> list[str]:
    engine = create_engine(site.target_url)
    try:
        with engine.connect() as connection:
            return list(connection.execute(text("SELECT name FROM workspaces")).scalars())
    finally:
        engine.dispose()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_manifest(archive: Path) -> dict:
    with zipfile.ZipFile(archive) as handle:
        return json.loads(handle.read("manifest.json"))


def _rewrite_manifest(archive: Path, target: Path, mutate) -> Path:
    """复制归档并改写清单（模拟被篡改或来自不同版本的包）。"""
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(target, "w") as destination:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "manifest.json":
                manifest = json.loads(data)
                mutate(manifest)
                data = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
            destination.writestr(info, data)
    return target


def _copy_without_manifest(archive: Path, target: Path) -> Path:
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(target, "w") as destination:
        for info in source.infolist():
            if info.filename == "manifest.json":
                continue
            destination.writestr(info, source.read(info.filename))
    return target


def _tamper_entry(archive: Path, target: Path, name: str, *, append: bool = True) -> Path:
    """改写一个条目：默认追加字节（大小不符），否则翻转首字节（只触发摘要不符）。"""
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(target, "w") as destination:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == name:
                data = data + b"tampered" if append else bytes([data[0] ^ 0xFF]) + data[1:]
            destination.writestr(info, data)
    return target


def _token_for(path: Path) -> str:
    return f"sha256:{_sha256(path)}"


# --------------------------------------------------------------------------- 用例


def test_export_import_roundtrip_preserves_rows_files_and_checksums(site: Site) -> None:
    settings = site.settings()
    result = export_workspace(settings, site.archive)

    assert result.counts == {
        "workspaces": 1,
        "semesters": 1,
        "courses": 1,
        "materials": 2,
        "material_versions": 2,
        "candidates": 1,
        "tasks": 1,
        "plans": 1,
        "time_blocks": 1,
        "indexed_blocks": 1,
    }
    assert result.confirm_token == _token_for(result.archive_path)
    assert result.material_files == len(MATERIALS)
    assert result.material_bytes == sum(len(content) for content in MATERIALS.values())

    with zipfile.ZipFile(result.archive_path) as handle:
        manifest = json.loads(handle.read("manifest.json"))
        names = handle.namelist()
        assert names[0] == "manifest.json"
        assert names[1] == "database.dump"
        assert names[2:] == sorted(f"materials/{path}" for path in MATERIALS)
        assert manifest["format_version"] == 1
        assert manifest["counts"] == result.counts
        expected_head = AlembicMigrator(str(BACKEND_DIR / "migrations")).head_revision()
        assert manifest["schema_head"] == expected_head
        assert (
            manifest["database"]["sha256"]
            == hashlib.sha256(handle.read("database.dump")).hexdigest()
        )
        assert manifest["database"]["size"] > 0
        excluded = {item["name"] for item in manifest["excluded_credentials"]}
        assert {"calendar_connections", "calendar_oauth_states"} <= excluded
        for entry in manifest["materials"]:
            payload = handle.read(f"materials/{entry['path']}")
            assert entry["size"] == len(payload)
            assert entry["sha256"] == hashlib.sha256(payload).hexdigest()
        assert not manifest["missing_referenced_files"]

    info = inspect_archive(result.archive_path)
    assert info.counts == result.counts
    assert info.requires_migration is False
    assert info.migration_message is None
    assert "calendar_connections" in info.requires_reauthorization
    assert "session_cookie" in info.requires_reauthorization

    # 不预建目标库：模拟新机器首次导入（导入内部负责建库、恢复与切换）
    imported = import_archive(
        site.target_settings(),
        result.archive_path,
        replace=True,
        confirm_token=info.confirm_token,
    )
    assert imported.restored_counts == result.counts
    assert imported.migrated_from == imported.migrated_to == info.schema_head
    assert imported.replaced_database is None
    assert imported.backup_archive is None
    assert any("未加密" in warning for warning in imported.warnings)
    assert any("重新录入" in warning for warning in imported.warnings)

    for relative, content in MATERIALS.items():
        restored = site.target_storage / relative
        assert restored.read_bytes() == content
        assert (
            hashlib.sha256(restored.read_bytes()).hexdigest() == hashlib.sha256(content).hexdigest()
        )

    engine = create_engine(site.target_url)
    try:
        with engine.connect() as connection:
            assert _counts(site.target_url) == result.counts
            assert (
                connection.execute(text("SELECT count(*) FROM calendar_connections")).scalar_one()
                == 0
            )
            assert (
                connection.execute(text("SELECT text FROM source_chunks")).scalar_one()
                == "作业 10 月 30 日 23:59 前提交"
            )
            assert connection.execute(text("SELECT title FROM tasks")).scalar_one() == "第一次作业"
            assert connection.execute(text("SELECT minutes FROM plan_blocks")).scalar_one() == 120
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == info.schema_head
            )
            assert connection.execute(
                text("SELECT value::text FROM extracted_fields WHERE name = 'title'")
            ).scalar_one() == json.dumps("第一次作业", ensure_ascii=False)
    finally:
        engine.dispose()

    # 源库不应被导入过程改写
    assert _counts(site.database_url) == result.counts


def test_material_paths_are_relative_posix_for_cross_os(site: Site) -> None:
    result = export_workspace(site.settings(), site.archive)
    manifest = _read_manifest(result.archive_path)
    paths = [entry["path"] for entry in manifest["materials"]]
    assert sorted(paths) == sorted(MATERIALS)
    for path in paths:
        assert not path.startswith("/")
        assert "\\" not in path
        assert ":" not in path  # 拒绝 Windows 盘符
        assert ".." not in PurePosixPath(path).parts
        assert len(PurePosixPath(path).parts) >= 2  # 保留年月层级

    info = inspect_archive(result.archive_path)
    import_archive(
        site.target_settings(), site.archive, replace=True, confirm_token=info.confirm_token
    )
    # 归档内相对路径在导入后原样落地，含中文、空格与多级目录
    for relative in MATERIALS:
        assert (site.target_storage / relative).is_file()
        assert (site.target_storage / relative).read_bytes() == MATERIALS[relative]


def test_import_requires_confirmation_token(site: Site) -> None:
    result = export_workspace(site.settings(), site.archive)
    with pytest.raises(ConfirmationRequiredError):
        import_archive(site.target_settings(), result.archive_path)
    # 没有令牌时连目标库都不应创建
    engine = _engine(site.admin_url, autocommit=True)
    try:
        with engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": site.target_name},
            ).scalar()
    finally:
        engine.dispose()
    assert not exists


def test_import_rejects_token_from_other_archive(site: Site) -> None:
    first = export_workspace(site.settings(), site.archive)
    # 改动一份资料后再导出一份，两份归档摘要必须不同
    changed = site.storage / "2026/10/lecture notes 01.txt"
    changed.write_bytes(changed.read_bytes() + b"v2")
    second = export_workspace(site.settings(), site.archive_dir / "second.campusflow")
    assert first.confirm_token != second.confirm_token

    with pytest.raises(ConfirmationMismatchError):
        import_archive(
            site.target_settings(),
            second.archive_path,
            replace=True,
            confirm_token=first.confirm_token,
        )
    # 连目标库都没有创建，更谈不上写入
    assert not _database_exists(site.admin_url, site.target_name)


def test_import_into_non_empty_target_needs_replace_and_keeps_backup(site: Site) -> None:
    result = export_workspace(site.settings(), site.archive)
    info = inspect_archive(result.archive_path)
    first = import_archive(
        site.target_settings(), site.archive, replace=True, confirm_token=info.confirm_token
    )
    assert first.restored_counts == result.counts

    with pytest.raises(TargetNotEmptyError):
        import_archive(site.target_settings(), site.archive, confirm_token=info.confirm_token)
    assert _counts(site.target_url) == result.counts

    replaced = import_archive(
        site.target_settings(),
        site.archive,
        replace=True,
        confirm_token=info.confirm_token,
    )
    assert replaced.restored_counts == result.counts
    assert replaced.replaced_database is not None
    assert replaced.backup_archive is not None and replaced.backup_archive.is_file()
    # 旧数据库保留在集群里，供失败回退或人工清理
    engine = _engine(site.admin_url, autocommit=True)
    try:
        with engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": replaced.replaced_database},
            ).scalar()
    finally:
        engine.dispose()
    assert exists
    # 导入前自动备份本身也是可检查的归档
    backup_info = inspect_archive(replaced.backup_archive)
    assert backup_info.counts == result.counts


def test_truncated_archive_is_rejected_without_touching_target(site: Site) -> None:
    result = export_workspace(site.settings(), site.archive)
    broken = site.archive_dir / "broken.campusflow"
    broken.write_bytes(result.archive_path.read_bytes()[: result.archive_size // 2])
    _sentinel_target(site)
    with pytest.raises(ArchiveFormatError):
        inspect_archive(broken)
    with pytest.raises(TransferError):
        import_archive(
            site.target_settings(), broken, replace=True, confirm_token=_token_for(broken)
        )
    assert _sentinel_names(site) == ["旧数据"]


def test_missing_manifest_is_rejected(site: Site) -> None:
    result = export_workspace(site.settings(), site.archive)
    broken = _copy_without_manifest(
        result.archive_path, site.archive_dir / "no-manifest.campusflow"
    )
    _sentinel_target(site)
    with pytest.raises(ArchiveFormatError, match="manifest"):
        inspect_archive(broken)
    with pytest.raises(ArchiveFormatError, match="manifest"):
        import_archive(
            site.target_settings(), broken, replace=True, confirm_token=_token_for(broken)
        )
    assert _sentinel_names(site) == ["旧数据"]


def test_checksum_mismatch_is_rejected(site: Site) -> None:
    result = export_workspace(site.settings(), site.archive)
    broken = _tamper_entry(
        result.archive_path,
        site.archive_dir / "tampered.campusflow",
        "database.dump",
        append=False,
    )
    with pytest.raises(ArchiveFormatError, match="SHA-256"):
        inspect_archive(broken)

    material = sorted(MATERIALS)[0]
    broken_material = _tamper_entry(
        result.archive_path,
        site.archive_dir / "tampered-material.campusflow",
        f"materials/{material}",
    )
    with pytest.raises(ArchiveFormatError, match="不符"):
        inspect_archive(broken_material)

    _sentinel_target(site)
    with pytest.raises(ArchiveFormatError):
        import_archive(
            site.target_settings(), broken, replace=True, confirm_token=_token_for(broken)
        )
    assert _sentinel_names(site) == ["旧数据"]


def test_format_version_too_new_is_rejected(site: Site) -> None:
    result = export_workspace(site.settings(), site.archive)

    def bump(manifest: dict) -> None:
        manifest["format_version"] = 2

    future = _rewrite_manifest(result.archive_path, site.archive_dir / "v2.campusflow", bump)
    _sentinel_target(site)
    with pytest.raises(ArchiveVersionError, match="格式版本"):
        inspect_archive(future)
    with pytest.raises(ArchiveVersionError):
        import_archive(
            site.target_settings(), future, replace=True, confirm_token=_token_for(future)
        )
    assert _sentinel_names(site) == ["旧数据"]


def test_schema_head_from_newer_app_is_rejected(site: Site) -> None:
    result = export_workspace(site.settings(), site.archive)

    def bump(manifest: dict) -> None:
        manifest["schema_head"] = "zzzz_future_revision"

    future = _rewrite_manifest(result.archive_path, site.archive_dir / "future.campusflow", bump)
    _sentinel_target(site)
    with pytest.raises(ArchiveVersionError, match="迁移范围"):
        inspect_archive(future)
    with pytest.raises(ArchiveVersionError):
        import_archive(
            site.target_settings(), future, replace=True, confirm_token=_token_for(future)
        )
    assert _sentinel_names(site) == ["旧数据"]


def test_older_schema_head_is_reported_as_forward_migration(site: Site) -> None:
    result = export_workspace(site.settings(), site.archive)
    migrator = AlembicMigrator(str(BACKEND_DIR / "migrations"))
    head = migrator.head_revision()
    older = sorted(migrator.known_revisions() - {head})[0]

    def downgrade_head(manifest: dict) -> None:
        manifest["schema_head"] = older

    older_archive = _rewrite_manifest(
        result.archive_path, site.archive_dir / "older.campusflow", downgrade_head
    )
    info = inspect_archive(older_archive)
    assert info.requires_migration is True
    assert older in (info.migration_message or "")
    assert head in (info.migration_message or "")

    imported = import_archive(
        site.target_settings(), older_archive, replace=True, confirm_token=info.confirm_token
    )
    assert imported.migrated_from == older
    assert imported.migrated_to == head
    assert imported.restored_counts == result.counts


def test_archive_contains_no_credentials(site: Site, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZHIPU_API_KEY", FAKE_API_KEY)
    monkeypatch.setenv("DEEPSEEK_API_KEY", FAKE_API_KEY)
    monkeypatch.setenv("CAMPUSFLOW_LOCAL_PASSWORD", FAKE_LOGIN_PASSWORD)
    result = export_workspace(site.settings(), site.archive)

    secrets_to_find = {
        "数据库口令": site.password.encode(),
        "OAuth 令牌": FAKE_OAUTH_TOKEN.encode(),
        "模型 API Key": FAKE_API_KEY.encode(),
        "本机登录口令": FAKE_LOGIN_PASSWORD.encode(),
    }
    with zipfile.ZipFile(result.archive_path) as handle:
        for name in handle.namelist():
            payload = handle.read(name)
            for label, needle in secrets_to_find.items():
                assert needle not in payload, f"{label} 出现在归档条目 {name} 中"
        manifest = json.loads(handle.read("manifest.json"))
    # 明文口令不在包里，但必须在清单中说明哪些凭据需要重新录入
    names = {item["name"] for item in manifest["excluded_credentials"]}
    assert {"calendar_connections", "calendar_oauth_states", "CAMPUSFLOW_LOCAL_PASSWORD"} <= names
    assert all(item["reason"] for item in manifest["excluded_credentials"])


@pytest.mark.parametrize("failure", ["restore", "migrate"])
def test_interrupted_import_leaves_original_data_intact(
    site: Site, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    result = export_workspace(site.settings(), site.archive)
    info = inspect_archive(result.archive_path)
    first = import_archive(
        site.target_settings(), site.archive, replace=True, confirm_token=info.confirm_token
    )
    assert first.restored_counts["tasks"] == 1
    sentinel = site.target_storage / sorted(MATERIALS)[0]
    assert sentinel.is_file()
    original_counts = _counts(site.target_url)

    service = build_transfer_service(site.target_settings())
    if failure == "restore":
        original_restore = service.tools.restore_database

        def explode(url: str, dump: Path) -> None:
            original_restore(url, dump)  # 临时库已写入完整数据
            raise RuntimeError("模拟恢复中断")

        monkeypatch.setattr(service.tools, "restore_database", explode)
    else:
        original_upgrade = service.migrator.upgrade_to_head

        def explode(url: str) -> None:
            original_upgrade(url)  # 迁移后中断
            raise RuntimeError("模拟迁移中断")

        monkeypatch.setattr(service.migrator, "upgrade_to_head", explode)

    with pytest.raises(RuntimeError, match="模拟"):
        service.import_archive(result.archive_path, replace=True, confirm_token=info.confirm_token)

    # 目标库与资料目录保持原样，临时库与工作目录都被清理
    assert _counts(site.target_url) == original_counts
    assert sentinel.read_bytes() == MATERIALS[sorted(MATERIALS)[0]]
    engine = _engine(site.admin_url, autocommit=True)
    try:
        with engine.connect() as connection:
            leftovers = connection.execute(
                text("SELECT datname FROM pg_database WHERE datname LIKE 'campusflow_import_%'")
            ).scalars()
            assert list(leftovers) == []
    finally:
        engine.dispose()
    assert not list(site.target_storage.parent.glob(".campusflow-import-*"))


def test_counts_mismatch_aborts_switch(site: Site) -> None:
    result = export_workspace(site.settings(), site.archive)

    def inflate(manifest: dict) -> None:
        manifest["counts"]["tasks"] = manifest["counts"]["tasks"] + 5

    tampered = _rewrite_manifest(
        result.archive_path, site.archive_dir / "counts.campusflow", inflate
    )
    _sentinel_target(site)
    with pytest.raises(ImportVerificationError, match="不一致"):
        import_archive(
            site.target_settings(), tampered, replace=True, confirm_token=_token_for(tampered)
        )
    assert _sentinel_names(site) == ["旧数据"]
    # 资料目录尚未被替换（旧目录不存在时也不会创建新的）
    assert not site.target_storage.exists()


def test_missing_material_file_is_reported_but_export_proceeds(site: Site) -> None:
    missing = "2026/10/lecture notes 01.txt"
    (site.storage / missing).unlink()
    result = export_workspace(site.settings(), site.archive)
    assert result.missing_referenced_files == (missing,)
    assert result.material_files == len(MATERIALS) - 1

    info = inspect_archive(result.archive_path)
    assert info.missing_referenced_files == (missing,)
    imported = import_archive(
        site.target_settings(), site.archive, replace=True, confirm_token=info.confirm_token
    )
    assert any("缺失" in warning for warning in imported.warnings)
    assert imported.restored_counts == result.counts
    assert not (site.target_storage / missing).exists()


def test_export_rejects_workspace_scoped_request(site: Site) -> None:
    with pytest.raises(TransferError, match="整库"):
        export_workspace(site.settings(), site.archive, workspace_id=1)


def test_export_requires_campusflow_suffix(site: Site) -> None:
    with pytest.raises(TransferError, match="campusflow"):
        export_workspace(site.settings(), site.archive_dir / "workspace.zip")
