"""J005 集成测试：分阶段失败重试与超时处理。

验收：
- 重试不重复写入已完成阶段；
- 超时后状态不会长期停在处理中。
"""

from datetime import UTC, datetime, timedelta

import pytest
from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from campusflow.application.jobs import reclaim_stale_jobs, retry_job
from campusflow.application.ports.parsers import (
    ParsedFragment,
    ParseFailure,
    ParseOutcome,
    ParserLocator,
)
from campusflow.application.processing import ParserRegistry, parse_version, set_parser_registry
from campusflow.domain.errors import PermanentError
from campusflow.domain.jobs import DEFAULT_JOB_TIMEOUT_SECONDS, is_retryable, timeout_reason
from campusflow.domain.states import JobStatus, MaterialStatus
from campusflow.infrastructure.llm.errors import VisionRecognitionError
from campusflow.infrastructure.llm.vision import FakeVisionParser
from campusflow.infrastructure.parsers.pdf import PdfDocumentParser
from campusflow.infrastructure.parsers.text import TextDocumentParser
from campusflow.infrastructure.repositories import (
    SqlAlchemyJobRepository,
    SqlAlchemyMaterialRepository,
    build_repositories,
)
from campusflow.infrastructure.storage.local import LocalFileStorage
from campusflow.main import reclaim_timed_out_jobs
from campusflow.workers.jobs import parse_version_job

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"retry-image"


class RecordingQueue:
    """只记录投递，不真的连接 Redis。"""

    def __init__(self) -> None:
        self.enqueued: list[int] = []

    def enqueue_parse(self, job_id: int) -> str:
        self.enqueued.append(job_id)
        return f"parse-{job_id}"


class TransientVisionParser:
    """模拟视觉模型临时故障：整次调用失败（限流/连接中断）。"""

    def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
        raise VisionRecognitionError("视觉模型调用失败：上游 503")


class PermanentVisionParser:
    """模拟永久失败：格式不支持，重试也不会成功。"""

    def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
        raise ValueError("图片格式不支持")


class StagedVisionParser:
    """模拟分阶段失败：第 1 页成功、第 2 页因临时故障未识别。"""

    def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
        return ParseOutcome(
            fragments=[
                ParsedFragment(
                    seq=0,
                    locator=ParserLocator(kind="page", page=1),
                    text="第一页识别成功的内容",
                )
            ],
            failures=[
                ParseFailure(
                    locator=ParserLocator(kind="page", page=2),
                    reason="第 2 页识别失败：连接中断",
                    retryable=True,
                )
            ],
        )


def _new_session() -> Session:
    return sessionmaker(bind=create_engine(TEST_DATABASE_URL))()


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


def _upload_image(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/materials/upload",
        files=[("files", ("通知.png", PNG_BYTES))],
    )
    assert response.status_code == 201, response.text
    return response.json()["results"][0]


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


def _chunks(material_id: int) -> list:
    session = _new_session()
    try:
        repo = SqlAlchemyMaterialRepository(session)
        version_id = repo.list_versions(material_id)[0].id
        return repo.list_chunks(version_id)
    finally:
        session.close()


def _material_status(material_id: int) -> str:
    session = _new_session()
    try:
        return SqlAlchemyMaterialRepository(session).get(material_id).status
    finally:
        session.close()


def _backdate_start(job_id: int, seconds: int) -> None:
    """把领取时间往前拨，模拟 Worker 卡死/被杀（不改业务字段）。"""
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE processing_jobs SET status = 'running', started_at = :started "
                "WHERE id = :job_id"
            ),
            {"started": datetime.now(UTC) - timedelta(seconds=seconds), "job_id": job_id},
        )


# ---------------------------------------------------------------- 可重试判定


def test_is_retryable_distinguishes_error_kinds() -> None:
    """临时故障可重试，永久失败与普通异常不可重试（J005 交付物）。"""
    assert is_retryable(VisionRecognitionError("上游 503")) is True
    assert is_retryable(PermanentError("来源已删除")) is False
    assert is_retryable(ValueError("格式不支持")) is False


def test_timeout_reason_is_readable() -> None:
    """超时说明要能直接给用户看，并说明已按可重试失败处理。"""
    reason = timeout_reason(DEFAULT_JOB_TIMEOUT_SECONDS)
    assert "15 分钟" in reason
    assert "可重试" in reason
    assert timeout_reason(0)  # 极端配置不产生除零或空文案


# ------------------------------------------------------- 临时故障 vs 永久失败


def test_transient_failure_marks_job_retryable(client: TestClient, shared_storage) -> None:
    """临时故障：作业标记失败但可重试，并留下一条尝试历史。"""
    _use_vision_parser(TransientVisionParser())
    result = _upload_image(client)
    job_id = result["job_id"]

    with pytest.raises(VisionRecognitionError):
        parse_version_job(job_id)

    job = _job(job_id)
    assert job.status == JobStatus.FAILED
    assert job.retryable is True
    assert "503" in (job.error_reason or "")
    assert _material_status(result["material"]["id"]) == MaterialStatus.FAILED

    attempts = _attempts(job_id)
    assert len(attempts) == 1
    assert attempts[0].attempt_no == 1
    assert attempts[0].status == JobStatus.FAILED
    assert attempts[0].retryable is True
    assert attempts[0].stage == "parse"


def test_permanent_failure_cannot_be_retried(client: TestClient, shared_storage) -> None:
    """永久失败：不标记可重试，重投入口直接拒绝，避免反复重投。"""
    _use_vision_parser(PermanentVisionParser())
    result = _upload_image(client)
    job_id = result["job_id"]

    with pytest.raises(ValueError):
        parse_version_job(job_id)

    job = _job(job_id)
    assert job.status == JobStatus.FAILED
    assert job.retryable is False

    session = _new_session()
    try:
        repos = build_repositories(session)
        queue = RecordingQueue()
        assert retry_job(repos, queue, job_id) is None
        assert queue.enqueued == []
    finally:
        session.close()


def test_source_missing_is_permanent_failure(client: TestClient, shared_storage) -> None:
    """来源已不可用属于永久失败（J005）：抛出永久错误且不可重试。"""
    _use_vision_parser(FakeVisionParser())
    result = _upload_image(client)
    job_id = result["job_id"]

    session = _new_session()
    try:
        repos = build_repositories(session)
        repo = SqlAlchemyMaterialRepository(session)
        version_id = repo.list_versions(result["material"]["id"])[0].id
        # 直接抹掉存储键，模拟原文件已不可用
        from campusflow.infrastructure.db.models import MaterialVersion

        session.get(MaterialVersion, version_id).storage_key = None
        session.commit()

        with pytest.raises(PermanentError):
            parse_version(repos, shared_storage, job_id)
    finally:
        session.close()

    job = _job(job_id)
    assert job.status == JobStatus.FAILED
    assert job.retryable is False
    assert job.error_reason == "来源已不可用"


# ------------------------------------------------------- 重试不重复已完成阶段


def test_retry_keeps_completed_stage_without_duplicates(client: TestClient, shared_storage) -> None:
    """分阶段失败重试：已成功页面的片段被复用，重试不重复写入（J005 验收）。"""
    _use_vision_parser(StagedVisionParser())
    result = _upload_image(client)
    job_id = result["job_id"]
    material_id = result["material"]["id"]

    parse_version_job(job_id)

    job = _job(job_id)
    assert job.status == JobStatus.PARTIAL
    assert job.retryable is True  # 失败范围是临时故障，值得重试补齐
    assert "第 2 页" in (job.scope or "")
    first_chunks = _chunks(material_id)
    assert [chunk.text for chunk in first_chunks] == ["第一页识别成功的内容"]

    # 重新处理：重置为待处理并交回待投递
    session = _new_session()
    try:
        queue = RecordingQueue()
        retried = retry_job(build_repositories(session), queue, job_id)
        assert retried is not None
        assert retried.status == JobStatus.PENDING
        assert retried.enqueued_at is not None
        assert queue.enqueued == [job_id]
    finally:
        session.close()
    assert _material_status(material_id) == MaterialStatus.PENDING

    # Worker 重跑：已完成阶段的片段数量与内容都不变
    parse_version_job(job_id)
    assert _chunks(material_id) == first_chunks

    job = _job(job_id)
    assert job.attempts == 2
    assert job.status == JobStatus.PARTIAL
    attempt_rows = _attempts(job_id)
    assert [row.attempt_no for row in attempt_rows] == [1, 2]
    assert all(row.status == JobStatus.PARTIAL for row in attempt_rows)


def test_retry_rejected_for_done_job(client: TestClient, shared_storage) -> None:
    """已完成的作业没有可重试的失败阶段，重投入口拒绝。"""
    _use_vision_parser(FakeVisionParser())
    job_id = _upload_image(client)["job_id"]
    parse_version_job(job_id)
    assert _job(job_id).status == JobStatus.DONE

    session = _new_session()
    try:
        queue = RecordingQueue()
        assert retry_job(build_repositories(session), queue, job_id) is None
        assert queue.enqueued == []
    finally:
        session.close()


# ----------------------------------------------------------------- 超时回收


def test_timeout_reclaims_stuck_running_job(client: TestClient, shared_storage) -> None:
    """超时回收：卡死的处理中作业被标记为可重试失败（J005 验收）。"""
    _use_vision_parser(FakeVisionParser())
    result = _upload_image(client)
    job_id = result["job_id"]
    material_id = result["material"]["id"]
    _backdate_start(job_id, seconds=1000)

    session = _new_session()
    try:
        reclaimed = reclaim_stale_jobs(build_repositories(session), timeout_seconds=900)
    finally:
        session.close()

    assert [job.id for job in reclaimed] == [job_id]
    job = _job(job_id)
    assert job.status == JobStatus.FAILED  # 不再停在"处理中"
    assert job.retryable is True
    assert "处理超时" in (job.error_reason or "")
    assert _material_status(material_id) == MaterialStatus.FAILED
    assert len(_attempts(job_id)) == 1


def test_recent_running_job_is_not_reclaimed(client: TestClient, shared_storage) -> None:
    """正在正常处理的作业不能被误回收（超时回收的反例）。"""
    _use_vision_parser(FakeVisionParser())
    job_id = _upload_image(client)["job_id"]
    _backdate_start(job_id, seconds=5)

    session = _new_session()
    try:
        assert reclaim_stale_jobs(build_repositories(session), timeout_seconds=900) == []
    finally:
        session.close()

    assert _job(job_id).status == JobStatus.RUNNING
    assert _attempts(job_id) == []


def test_startup_hook_reclaims_timed_out_jobs(client: TestClient, shared_storage) -> None:
    """启动钩子会回收超时作业，且不阻塞服务启动（J005 验收）。"""
    _use_vision_parser(FakeVisionParser())
    job_id = _upload_image(client)["job_id"]
    _backdate_start(job_id, seconds=DEFAULT_JOB_TIMEOUT_SECONDS + 60)

    assert reclaim_timed_out_jobs(client.app) == 1
    assert _job(job_id).status == JobStatus.FAILED


def test_sweep_is_best_effort_when_database_is_gone(client: TestClient, monkeypatch) -> None:
    """扫描失败只记录日志，不影响启动（与投递补偿一致的容错口径）。"""

    class BrokenSessionFactory:
        def __call__(self):
            raise RuntimeError("数据库不可达")

    monkeypatch.setattr(client.app.state, "session_factory", BrokenSessionFactory())
    assert reclaim_timed_out_jobs(client.app) == 0
