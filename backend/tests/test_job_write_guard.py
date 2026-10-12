"""J007 集成测试：晚到的作业不得写回已删除的资料或已被取代的版本。

验收：
- 删除中或删除后的晚到 Worker 不恢复资料；
- 新版本不能接收旧版本的结果。
"""

import pytest
from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from campusflow.application.ports.parsers import (
    ParsedFragment,
    ParseOutcome,
    ParserLocator,
)
from campusflow.application.processing import ParserRegistry, set_parser_registry
from campusflow.domain.materials import (
    DELETED_MATERIAL_REASON,
    SUPERSEDED_VERSION_REASON,
    is_superseded,
)
from campusflow.domain.states import JobStatus, MaterialStatus
from campusflow.infrastructure.db.models import Material, ProcessingJob
from campusflow.infrastructure.llm.vision import FakeVisionParser
from campusflow.infrastructure.parsers.pdf import PdfDocumentParser
from campusflow.infrastructure.parsers.text import TextDocumentParser
from campusflow.infrastructure.repositories import (
    SqlAlchemyJobRepository,
    SqlAlchemyMaterialRepository,
    build_repositories,
)
from campusflow.infrastructure.storage.local import LocalFileStorage
from campusflow.workers.jobs import parse_version_job

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"write-guard-image"

# 直接查表用的语句：统计行数，绕过 ORM 会话缓存看数据库里的真相
MATERIAL_ROWS = "SELECT count(*) FROM materials WHERE id = :id"
MATERIAL_VERSION_ROWS = "SELECT count(*) FROM material_versions WHERE material_id = :id"
MATERIAL_JOB_ROWS = "SELECT count(*) FROM processing_jobs WHERE material_id = :id"
JOB_ROWS = "SELECT count(*) FROM processing_jobs WHERE id = :id"
JOB_ATTEMPT_ROWS = "SELECT count(*) FROM processing_job_attempts WHERE job_id = :id"
CHUNK_ROWS = "SELECT count(*) FROM source_chunks"


class RecordingQueue:
    """只记录投递，不真的连接 Redis。"""

    def __init__(self) -> None:
        self.enqueued: list[int] = []

    def enqueue_parse(self, job_id: int) -> str:
        self.enqueued.append(job_id)
        return f"parse-{job_id}"


class CountingVisionParser:
    """统计视觉模型调用次数：付费调用不该发生在注定作废的作业上。"""

    def __init__(self) -> None:
        self.calls = 0

    def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
        self.calls += 1
        return ParseOutcome(
            fragments=[
                ParsedFragment(
                    seq=0,
                    locator=ParserLocator(kind="region", bbox=(0.1, 0.1, 0.9, 0.3)),
                    text="识别出的内容",
                    confidence=0.9,
                )
            ],
            failures=[],
        )


class DeletingDuringParseParser:
    """模拟"解析还没结束，用户就把资料删了"。

    删除走真实的 HTTP 接口（另一条独立的数据库连接），因此和真实并发一样，
    Worker 所在会话并不知道行已经没了——这正是 J007 要挡住的场景。
    """

    def __init__(self, client: TestClient, holder: dict) -> None:
        self._client = client
        self._holder = holder
        self.deleted = False

    def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
        material_id = self._holder["material_id"]
        assert material_id is not None, "测试自身有误：尚未记录资料 id"
        _delete_material(self._client, material_id)
        self.deleted = True
        return ParseOutcome(
            fragments=[
                ParsedFragment(
                    seq=0,
                    locator=ParserLocator(kind="page", page=1),
                    text="资料删除后才解析出来的内容，不该出现在任何地方",
                )
            ],
            failures=[],
        )


def _new_session() -> Session:
    return sessionmaker(bind=create_engine(TEST_DATABASE_URL))()


def _scalar(sql: str, **params):
    """直接查表取标量，绕过 ORM 会话缓存看数据库里的真相。"""
    engine = create_engine(TEST_DATABASE_URL)
    with engine.connect() as connection:
        return connection.execute(text(sql), params).scalar_one()


def _count(sql: str, **params) -> int:
    return int(_scalar(sql, **params))


@pytest.fixture
def shared_storage(client: TestClient, tmp_path, monkeypatch):
    storage = LocalFileStorage(tmp_path)
    client.app.state.file_storage = storage
    monkeypatch.setenv("CAMPUSFLOW_STORAGE_DIR", str(tmp_path))
    return storage


@pytest.fixture(autouse=True)
def local_queue(client: TestClient):
    """测试不依赖真实 Redis：上传时的投递走记录队列，结束后还原。"""
    original = client.app.state.job_queue
    client.app.state.job_queue = RecordingQueue()
    yield
    client.app.state.job_queue = original


def _use_vision_parser(parser) -> None:
    set_parser_registry(
        ParserRegistry(
            text_parser=TextDocumentParser(),
            pdf_parser=PdfDocumentParser(),
            vision_parser=parser,
        )
    )


def _upload_image(client: TestClient, *, tag: bytes = b"") -> dict:
    response = client.post(
        "/api/v1/materials/upload",
        files=[("files", ("通知.png", PNG_BYTES + tag))],
    )
    assert response.status_code == 201, response.text
    result = response.json()["results"][0]
    assert result["job_id"] is not None
    return result


def _delete_rows_directly(material_id: int) -> None:
    """绕过接口直接在另一条连接上删除资料，模拟删除发生在 Worker 读完之后。"""
    session = _new_session()
    try:
        session.execute(text("DELETE FROM materials WHERE id = :id"), {"id": material_id})
        session.commit()
    finally:
        session.close()


def _delete_material(client: TestClient, material_id: int) -> None:
    """按 D008 流程删除资料：先取影响面确认，再执行删除。"""
    impact = client.get(f"/api/v1/materials/{material_id}/delete-impact")
    assert impact.status_code == 200, impact.text
    response = client.post(
        f"/api/v1/materials/{material_id}/delete",
        json={"confirm_token": impact.json()["confirm_token"], "keep_tasks": True},
    )
    assert response.status_code == 204, response.text


def _job(job_id: int):
    session = _new_session()
    try:
        return SqlAlchemyJobRepository(session).get(job_id)
    finally:
        session.close()


def _attempts(job_id: int) -> list:
    session = _new_session()
    try:
        return SqlAlchemyJobRepository(session).list_attempts(job_id)
    finally:
        session.close()


def _material_status(material_id: int) -> str | None:
    session = _new_session()
    try:
        material = SqlAlchemyMaterialRepository(session).get(material_id)
        return material.status if material else None
    finally:
        session.close()


def _chunks_of_material(material_id: int) -> list:
    session = _new_session()
    try:
        repo = SqlAlchemyMaterialRepository(session)
        chunks = []
        for version in repo.list_versions(material_id):
            chunks.extend(repo.list_chunks(version.id))
        return chunks
    finally:
        session.close()


# ------------------------------------------------------------------ 领域规则


def test_superseded_rule_only_marks_older_versions() -> None:
    """只有比当前最新版本旧的版本才算被取代（J007）。"""
    assert is_superseded(1, 2) is True
    assert is_superseded(2, 2) is False  # 最新版本自己不算被取代
    assert is_superseded(3, 2) is False  # 历史更久的版本不受影响
    assert DELETED_MATERIAL_REASON and SUPERSEDED_VERSION_REASON


# ------------------------------------------------------- 晚到 Worker 不复活资料


def test_late_worker_after_delete_does_not_resurrect_material(
    client: TestClient, shared_storage
) -> None:
    """资料删除后，消息队列里迟到的作业不能把资料写回来（J007 验收）。"""
    _use_vision_parser(FakeVisionParser())
    result = _upload_image(client)
    material_id = result["material"]["id"]
    job_id = result["job_id"]

    _delete_material(client, material_id)
    # 作业随资料级联删除：DELETE 不再被外键卡住，也不留下指向已删资料的作业
    assert _count(MATERIAL_ROWS, id=material_id) == 0
    assert _count(JOB_ROWS, id=job_id) == 0

    # 迟到的 Worker 照常执行这条消息：一笔都不能写回
    parse_version_job(job_id)

    assert _count(MATERIAL_ROWS, id=material_id) == 0
    assert _count(MATERIAL_VERSION_ROWS, id=material_id) == 0
    assert _count(MATERIAL_JOB_ROWS, id=material_id) == 0
    assert _chunks_of_material(material_id) == []


def test_deletion_during_parse_blocks_write_back(client: TestClient, shared_storage) -> None:
    """解析过程中资料被删除：写入前的核对必须拦下这次写回（J007 验收）。"""
    holder: dict = {"material_id": None}
    parser = DeletingDuringParseParser(client, holder)
    _use_vision_parser(parser)

    result = _upload_image(client)
    holder["material_id"] = result["material"]["id"]
    job_id = result["job_id"]

    parse_version_job(job_id)  # 解析中途删除发生，Worker 正常跑完不抛异常

    assert parser.deleted is True  # 确认删除真的发生在解析过程中
    material_id = holder["material_id"]
    assert _count(MATERIAL_ROWS, id=material_id) == 0
    assert _count(MATERIAL_VERSION_ROWS, id=material_id) == 0
    # 片段的真相：整库都不该出现这条迟到的内容
    assert _count(CHUNK_ROWS) == 0
    assert _count("SELECT count(*) FROM processing_job_attempts") == 0
    assert _job(job_id) is None


# --------------------------------------------------- 新版本不接收旧版本结果


def test_old_version_job_does_not_write_into_new_version(
    client: TestClient, shared_storage
) -> None:
    """资料出了新版本后，旧版本的作业结果不写回（J007 验收）。"""
    _use_vision_parser(FakeVisionParser())
    result = _upload_image(client)
    material_id = result["material"]["id"]
    job_id = result["job_id"]

    # 同一份资料上传了更新的版本（版本 2）
    session = _new_session()
    try:
        repos = build_repositories(session)
        repos.materials.create_version(material_id, 2, "更新版", None)
        repos.uow.commit()
    finally:
        session.close()

    parse_version_job(job_id)

    job = _job(job_id)
    assert job is not None
    assert job.status == JobStatus.FAILED
    assert job.error_reason == SUPERSEDED_VERSION_REASON
    assert job.retryable is False  # 永久作废，重试也不会变成新版本的结果
    attempt_rows = _attempts(job_id)
    assert len(attempt_rows) == 1
    assert attempt_rows[0].retryable is False

    # 旧版本没有写入任何片段，新版本的处理状态也没被旧结果覆盖
    assert _chunks_of_material(material_id) == []
    assert _material_status(material_id) == MaterialStatus.PENDING


def test_superseded_job_skips_paid_vision_call(client: TestClient, shared_storage) -> None:
    """已被取代的作业在解析前就被作废，不浪费付费的视觉识别调用（J007）。"""
    parser = CountingVisionParser()
    _use_vision_parser(parser)
    result = _upload_image(client)
    material_id = result["material"]["id"]
    job_id = result["job_id"]

    session = _new_session()
    try:
        repos = build_repositories(session)
        repos.materials.create_version(material_id, 2, "更新版", None)
        repos.uow.commit()
    finally:
        session.close()

    parse_version_job(job_id)

    assert parser.calls == 0
    assert _job(job_id).error_reason == SUPERSEDED_VERSION_REASON


def test_latest_version_job_still_writes_normally(client: TestClient, shared_storage) -> None:
    """反例：作业目标就是最新版本时照常写入，核对不能误伤正常作业。"""
    _use_vision_parser(FakeVisionParser())
    result = _upload_image(client)
    material_id = result["material"]["id"]
    job_id = result["job_id"]

    parse_version_job(job_id)

    job = _job(job_id)
    assert job.status == JobStatus.DONE
    assert job.error_reason is None
    assert len(_chunks_of_material(material_id)) == 1
    assert _material_status(material_id) == MaterialStatus.DONE


# ------------------------------------------------------------- 删除的连带清理


def test_delete_material_with_processing_job_removes_job_and_attempts(
    client: TestClient, shared_storage
) -> None:
    """资料删除连带清理它的作业与尝试历史（J007）：删除不再被外键卡住。"""
    _use_vision_parser(FakeVisionParser())
    result = _upload_image(client, tag=b"-attempts")
    material_id = result["material"]["id"]
    job_id = result["job_id"]

    parse_version_job(job_id)
    assert len(_attempts(job_id)) == 1  # 先造出一条尝试历史

    _delete_material(client, material_id)

    assert _count(JOB_ROWS, id=job_id) == 0
    assert _count(JOB_ATTEMPT_ROWS, id=job_id) == 0
    assert _count(CHUNK_ROWS) == 0
    assert _attempts(job_id) == []


def test_cached_rows_cannot_be_written_back_after_delete(
    client: TestClient, shared_storage
) -> None:
    """会话里还留着已删除的资料与作业时，改写必须安静放弃（J007）。

    这是"晚到的 Worker"最直接的一种形态：作业与资料已经读进会话（代码里还持有
    这两行对象），用户在另一条连接上把资料删了。此时任何改写都不能落库——没有
    写入前的存在性核对，SQLAlchemy 会拿缓存里的行打出 0 行的 UPDATE 并抛
    StaleDataError，把"放弃写入"变成一次莫名其妙的崩溃。
    """
    _use_vision_parser(FakeVisionParser())
    result = _upload_image(client, tag=b"-cached")
    material_id = result["material"]["id"]
    job_id = result["job_id"]

    session = _new_session()
    try:
        repos = build_repositories(session)
        # Worker 的会话：作业与资料已经读进来，且代码里还持有这两行对象
        cached_material = session.get(Material, material_id)
        cached_job = session.get(ProcessingJob, job_id)
        assert cached_material is not None and cached_job is not None
        assert repos.materials.get(material_id) is not None
        assert repos.jobs.get(job_id) is not None

        _delete_rows_directly(material_id)

        assert (
            repos.jobs.record_attempt(
                job_id, status=JobStatus.FAILED, scope=None, error_reason="迟到的结果"
            )
            is None
        )
        assert repos.materials.set_status(material_id, MaterialStatus.FAILED) is None
        assert repos.jobs.record_delivery(job_id, delivered=True) is None
        assert repos.jobs.prepare_retry(job_id) is None
        repos.uow.commit()  # 没有任何悬挂的写入被提交
    finally:
        session.close()

    assert _count(MATERIAL_ROWS, id=material_id) == 0
    assert _count(MATERIAL_VERSION_ROWS, id=material_id) == 0
    assert _count(JOB_ROWS, id=job_id) == 0
    assert _count(JOB_ATTEMPT_ROWS, id=job_id) == 0
    assert _count(CHUNK_ROWS) == 0


def test_dispatch_skips_deleted_job(client: TestClient, shared_storage) -> None:
    """资料在投递前被删除：投递登记落空但不报错（J007 容错口径）。"""
    _use_vision_parser(FakeVisionParser())
    session = _new_session()
    try:
        repos = build_repositories(session)
        queue = RecordingQueue()
        # 直接对一个不存在的作业登记投递：必须安静返回 None，不抛 StaleDataError
        assert repos.jobs.record_delivery(999_999, delivered=True) is None
        assert (
            repos.jobs.record_attempt(
                999_999, status=JobStatus.FAILED, scope=None, error_reason="作业不存在"
            )
            is None
        )
        assert repos.materials.set_status(999_999, MaterialStatus.FAILED) is None
        assert repos.jobs.prepare_retry(999_999) is None
        assert queue.enqueued == []
    finally:
        session.close()
