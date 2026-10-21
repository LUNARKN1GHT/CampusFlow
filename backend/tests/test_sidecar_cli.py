"""P006：sidecar 命令入口的迁移、备份、恢复与诊断。

用例使用为本轮创建的临时数据库（名称以 `_test` 结尾），不读写共享的
`campusflow_test`。建库需要可执行 CREATE DATABASE 的管理员连接：
优先 `CAMPUSFLOW_TEST_ADMIN_DATABASE_URL`，其次本机 unix socket 的
`postgresql+psycopg:///postgres`（以当前操作系统用户连接）。环境不具备
建库权限时相关用例跳过，其余单元断言仍然执行。
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert, text
from sqlalchemy.engine import make_url

from campusflow.core.config import Settings
from campusflow.infrastructure.db.models import (
    Course,
    ExtractedItem,
    Material,
    MaterialVersion,
    Semester,
    Task,
    Workspace,
)
from campusflow.sidecar import cli
from campusflow.sidecar.backup import read_manifest
from campusflow.sidecar.dbtools import script_heads

BACKEND_DIR = Path(__file__).resolve().parent.parent
BASE_DATABASE_URL = os.environ.get(
    "CAMPUSFLOW_TEST_DATABASE_URL",
    "postgresql+psycopg://campusflow:campusflow@127.0.0.1:5432/campusflow_test",
)
ADMIN_DATABASE_URL = os.environ.get(
    "CAMPUSFLOW_TEST_ADMIN_DATABASE_URL", "postgresql+psycopg:///postgres"
)
MODEL_KEY = "sk-p006-distinctive-model-key"
COUNTED_TABLES = (
    "workspaces",
    "semesters",
    "courses",
    "materials",
    "material_versions",
    "extracted_items",
    "tasks",
)


@dataclass
class ScratchDatabase:
    name: str
    url: str


def _url_for(database: str, *, base: str = BASE_DATABASE_URL) -> str:
    return make_url(base).set(database=database).render_as_string(hide_password=False)


def _admin_engine(database: str = "postgres"):
    return create_engine(
        _url_for(database, base=ADMIN_DATABASE_URL),
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 3},
    )


def _create_database(*, with_vector: bool = True) -> ScratchDatabase:
    """建一个临时库（可选预装 vector 扩展）；没有管理员权限时跳过用例。"""
    name = f"campusflow_p006_{uuid.uuid4().hex[:8]}_test"
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
                # 普通业务角色无权创建扩展，安装包应由管理员预先建好。
                connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        finally:
            engine.dispose()
    return ScratchDatabase(name=name, url=_url_for(name))


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


def _use_database(
    monkeypatch: pytest.MonkeyPatch, database: ScratchDatabase, storage: Path
) -> None:
    """把 sidecar 读到的环境变量指向指定临时库与存储目录。"""
    storage.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CAMPUSFLOW_DATABASE_URL", database.url)
    monkeypatch.setenv("CAMPUSFLOW_STORAGE_DIR", str(storage))
    monkeypatch.setenv("CAMPUSFLOW_DEPLOYMENT_MODE", "server")
    monkeypatch.delenv("CAMPUSFLOW_DATA_DIR", raising=False)


def _head_revision(database: ScratchDatabase) -> str:
    settings = Settings(_env_file=None, database_url=database.url)
    heads = script_heads(settings)
    assert len(heads) == 1
    return heads[0]


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


def _table_counts(url: str) -> dict[str, int]:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return {
                table: connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
                for table in COUNTED_TABLES
            }
    finally:
        engine.dispose()


def _storage_listing(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _seed(database: ScratchDatabase, storage: Path) -> None:
    """写入工作空间／课程／资料／候选／任务，并在存储目录放对应文件。"""
    for relative, content in (
        ("2026/10/outline.txt", b"course outline"),
        ("2026/10/notice.txt", b"notice body"),
        ("2026/11/attachment.txt", b"attachment body"),
    ):
        target = storage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    engine = create_engine(database.url)
    try:
        with engine.begin() as connection:
            workspace_id = connection.execute(
                insert(Workspace.__table__).values(name="P006 验证空间", is_default=True)
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
                    workspace_id=workspace_id, semester_id=semester_id, name="高等数学"
                )
            ).inserted_primary_key[0]
            material_id = connection.execute(
                insert(Material.__table__).values(
                    workspace_id=workspace_id,
                    semester_id=semester_id,
                    course_id=course_id,
                    title="课程大纲",
                    source_type="paste",
                )
            ).inserted_primary_key[0]
            version_id = connection.execute(
                insert(MaterialVersion.__table__).values(
                    material_id=material_id,
                    version_no=1,
                    storage_key="2026/10/outline.txt",
                    checksum="0" * 64,
                )
            ).inserted_primary_key[0]
            for _ in range(2):
                connection.execute(
                    insert(ExtractedItem.__table__).values(
                        workspace_id=workspace_id, source_version_id=version_id, kind="task"
                    )
                )
            for index in range(3):
                connection.execute(
                    insert(Task.__table__).values(
                        workspace_id=workspace_id,
                        course_id=course_id,
                        title=f"完成习题{index}",
                    )
                )
    finally:
        engine.dispose()


def test_migrate_is_idempotent_and_check_reports_head(
    database_factory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    database = database_factory()
    _use_database(monkeypatch, database, tmp_path / "materials")

    assert cli.main(["migrate"]) == 0
    assert "迁移完成" in capsys.readouterr().out
    assert _alembic_revision(database.url) == _head_revision(database)

    # 第二次执行必须成功且不改变版本：已是最新时 upgrade head 是空操作。
    assert cli.main(["migrate"]) == 0
    assert _alembic_revision(database.url) == _head_revision(database)

    capsys.readouterr()
    assert cli.main(["migrate", "--check", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["at_head"] is True
    assert payload["head"] == [_head_revision(database)]
    assert payload["current"] == payload["head"]


def test_migrate_check_reports_behind_on_empty_database(
    database_factory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    database = database_factory()
    _use_database(monkeypatch, database, tmp_path / "materials")

    assert cli.main(["migrate", "--check"]) == 1
    output = capsys.readouterr().out
    assert "未就绪" in output
    assert "current=（空）" in output
    # --check 不写库：空库仍然没有 alembic_version 记录。
    assert _alembic_revision(database.url) is None


def test_backup_and_restore_round_trip(
    database_factory, monkeypatch, tmp_path: Path, capsys
) -> None:
    source = database_factory()
    target = database_factory()
    source_storage = tmp_path / "source-materials"
    target_storage = tmp_path / "target-materials"
    _use_database(monkeypatch, source, source_storage)

    assert cli.main(["migrate"]) == 0
    _seed(source, source_storage)
    capsys.readouterr()

    archive = tmp_path / "campusflow-backup.tar"
    assert cli.main(["backup", "--output", str(archive)]) == 0
    assert archive.is_file()
    assert "备份完成" in capsys.readouterr().out

    manifest = read_manifest(archive)
    assert manifest["archive_format_version"] == 1
    assert manifest["database"] == source.name
    assert manifest["alembic_head"] == [_head_revision(source)]
    assert manifest["storage_file_count"] == 3
    raw_manifest = json.dumps(manifest)
    assert "campusflow:campusflow@" not in raw_manifest
    assert ":***@" in manifest["database_url"]
    assert "password" not in raw_manifest.lower()

    source_counts = _table_counts(source.url)
    assert source_counts["materials"] == 1
    assert source_counts["tasks"] == 3
    assert source_counts["extracted_items"] == 2
    source_files = _storage_listing(source_storage)

    _use_database(monkeypatch, target, target_storage)
    assert cli.main(["restore", str(archive)]) == 0
    assert "恢复完成" in capsys.readouterr().out

    assert _table_counts(target.url) == source_counts
    assert _storage_listing(target_storage) == source_files
    assert _storage_listing(target_storage)

    # 恢复后迁移头与备份时一致，后续迁移不会从空库重放。
    assert _alembic_revision(target.url) == _head_revision(target)


def test_restore_requires_replace_and_keeps_existing_data(
    database_factory, monkeypatch, tmp_path: Path, capsys
) -> None:
    source = database_factory()
    target = database_factory()
    source_storage = tmp_path / "source-materials"
    target_storage = tmp_path / "target-materials"
    _use_database(monkeypatch, source, source_storage)
    assert cli.main(["migrate"]) == 0
    _seed(source, source_storage)
    archive = tmp_path / "campusflow-backup.tar"
    assert cli.main(["backup", "--output", str(archive)]) == 0

    _use_database(monkeypatch, target, target_storage)
    assert cli.main(["restore", str(archive)]) == 0
    before = _table_counts(target.url)
    capsys.readouterr()

    # 非空库不给 --replace 必须拒绝，且不改动任何数据。
    assert cli.main(["restore", str(archive)]) == 1
    assert "--replace" in capsys.readouterr().err
    assert _table_counts(target.url) == before

    assert cli.main(["restore", str(archive), "--replace"]) == 0
    output = capsys.readouterr().out
    assert "安全备份" in output
    safety = sorted(tmp_path.glob("campusflow-safety-*.tar"))
    assert safety, "覆盖式恢复必须先写安全备份"
    assert read_manifest(safety[0])["database"] == target.name
    assert _table_counts(target.url) == _table_counts(source.url)


def test_doctor_reports_ok_without_leaking_secrets(
    database_factory, monkeypatch, tmp_path: Path, capsys
) -> None:
    database = database_factory()
    _use_database(monkeypatch, database, tmp_path / "materials")
    assert cli.main(["migrate"]) == 0
    monkeypatch.setenv("ZHIPU_API_KEY", MODEL_KEY)
    capsys.readouterr()

    assert cli.main(["doctor", "--json"]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["ok"] is True
    assert report["checks"]["database"]["ok"] is True
    assert report["checks"]["vector_extension"]["version"]
    assert report["checks"]["migrations"]["status"] == "current"
    assert report["checks"]["pg_tools"]["ok"] is True
    assert report["checks"]["model_key"]["configured"] is True
    combined = captured.out + captured.err
    assert MODEL_KEY not in combined
    assert "campusflow:campusflow@" not in combined
    assert ":campusflow@" not in combined


def test_doctor_flags_missing_extension_and_data_dir(
    database_factory, monkeypatch, tmp_path: Path, capsys
) -> None:
    # 空库且没有 vector 扩展：数据库可达，但扩展与迁移两项都不满足。
    database = database_factory(with_vector=False)
    _use_database(monkeypatch, database, tmp_path / "materials")
    missing_data_dir = tmp_path / "missing-data"
    monkeypatch.setenv("CAMPUSFLOW_DATA_DIR", str(missing_data_dir))
    capsys.readouterr()

    assert cli.main(["doctor", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert report["checks"]["database"]["ok"] is True
    assert report["checks"]["vector_extension"]["ok"] is False
    assert report["checks"]["vector_extension"]["status"] == "missing"
    assert report["checks"]["migrations"]["ok"] is False
    assert report["checks"]["data_dir"]["ok"] is False
    assert report["checks"]["data_dir"]["status"] == "missing"
    assert set(report["critical_failed"]) >= {"vector_extension", "migrations", "data_dir"}


def test_usage_errors_return_exit_code_two(capsys) -> None:
    assert cli.main([]) == 2
    assert cli.main(["no-such-command"]) == 2
    assert cli.main(["backup"]) == 2  # 缺少必需的 --output
    assert cli.main(["serve", "--port", "70000"]) == 2
    assert "端口" in capsys.readouterr().err


def test_runtime_failures_return_exit_code_one(
    database_factory, monkeypatch, tmp_path: Path, capsys
) -> None:
    database = database_factory()
    _use_database(monkeypatch, database, tmp_path / "materials")

    assert cli.main(["restore", str(tmp_path / "missing.tar")]) == 1
    assert "归档不存在" in capsys.readouterr().err

    # 数据库不可达：运行失败而不是参数错误。
    monkeypatch.setenv(
        "CAMPUSFLOW_DATABASE_URL",
        "postgresql+psycopg://campusflow:campusflow@127.0.0.1:1/campusflow_unreachable_test",
    )
    assert cli.main(["migrate"]) == 1
    assert "无法连接数据库" in capsys.readouterr().err


def test_backup_refuses_to_overwrite_existing_archive(
    database_factory, monkeypatch, tmp_path: Path, capsys
) -> None:
    database = database_factory()
    _use_database(monkeypatch, database, tmp_path / "materials")
    assert cli.main(["migrate"]) == 0
    archive = tmp_path / "campusflow-backup.tar"
    assert cli.main(["backup", "--output", str(archive)]) == 0
    size = archive.stat().st_size
    capsys.readouterr()

    assert cli.main(["backup", "--output", str(archive)]) == 1
    assert "已存在" in capsys.readouterr().err
    assert archive.stat().st_size == size
    assert cli.main(["backup", "--output", str(archive), "--overwrite"]) == 0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_serve_answers_health_and_exits_cleanly(
    database_factory, monkeypatch, tmp_path: Path
) -> None:
    database = database_factory()
    _use_database(monkeypatch, database, tmp_path / "materials")
    assert cli.main(["migrate"]) == 0

    port = _free_port()
    env = os.environ.copy()
    process = subprocess.Popen(
        [sys.executable, "-m", "campusflow.sidecar.cli", "serve", "--port", str(port)],
        cwd=BACKEND_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 60
        response = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(f"serve 提前退出：{process.stderr.read() if process.stderr else ''}")
            try:
                response = urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/v1/health", timeout=1
                )
            except (urllib.error.URLError, ConnectionError, OSError):
                time.sleep(0.2)
                continue
            break
        assert response is not None, "serve 未在 60 秒内响应健康检查"
        assert json.loads(response.read()) == {"status": "ok", "service": "campusflow-api"}
        response.close()
    finally:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
            pytest.fail("serve 收到 SIGTERM 后没有在 30 秒内退出")
    assert process.returncode == 0, "优雅关闭应以 0 退出"
