"""J004 集成测试：待投递补偿与作业幂等领取。

验收：
- 重复投递不重复生成片段或候选结果；
- 投递中断后可恢复（启动补偿扫描重投待投递记录）。
"""

import fakeredis
import pytest
from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from rq import Queue, SimpleWorker
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from campusflow.application.processing import ParserRegistry, set_parser_registry
from campusflow.domain.states import JobStatus, MaterialStatus
from campusflow.infrastructure.llm.vision import FakeVisionParser
from campusflow.infrastructure.parsers.pdf import PdfDocumentParser
from campusflow.infrastructure.parsers.text import TextDocumentParser
from campusflow.infrastructure.queue.rq_queue import QUEUE_NAME, RQJobQueue, rq_job_key
from campusflow.infrastructure.repositories import (
    SqlAlchemyJobRepository,
    SqlAlchemyMaterialRepository,
)
from campusflow.main import compensate_pending_deliveries
from campusflow.workers.jobs import parse_version_job

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"idempotent-image"


class BrokenQueue:
    def enqueue_parse(self, job_id: int) -> str:
        raise ConnectionError("redis 不可用")


class RecordingQueue:
    def __init__(self) -> None:
        self.enqueued: list[int] = []

    def enqueue_parse(self, job_id: int) -> str:
        self.enqueued.append(job_id)
        return rq_job_key(job_id)


def _new_session() -> Session:
    return sessionmaker(bind=create_engine(TEST_DATABASE_URL))()


@pytest.fixture
def shared_storage(client: TestClient, tmp_path, monkeypatch):
    """API 与 Worker 共用同一存储目录，Worker 从配置读取。"""
    from campusflow.infrastructure.storage.local import LocalFileStorage

    storage = LocalFileStorage(tmp_path)
    client.app.state.file_storage = storage
    monkeypatch.setenv("CAMPUSFLOW_STORAGE_DIR", str(tmp_path))
    return storage


@pytest.fixture(autouse=True)
def text_registry() -> None:
    set_parser_registry(
        ParserRegistry(
            text_parser=TextDocumentParser(),
            pdf_parser=PdfDocumentParser(),
            vision_parser=FakeVisionParser(),
        )
    )


def _upload_image(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/materials/upload",
        files=[("files", ("通知.png", PNG_BYTES))],
    )
    assert response.status_code == 201, response.text
    return response.json()["results"][0]


def _chunks(version_id: int) -> list:
    session = _new_session()
    try:
        return SqlAlchemyMaterialRepository(session).list_chunks(version_id)
    finally:
        session.close()


def _job_row(job_id: int):
    session = _new_session()
    try:
        return SqlAlchemyJobRepository(session).get(job_id)
    finally:
        session.close()


def test_duplicate_message_does_not_duplicate_chunks(client: TestClient, shared_storage) -> None:
    """重复投递：第二次执行被幂等领取挡下，片段不会翻倍（J004 验收）。"""
    client.app.state.job_queue = RecordingQueue()
    result = _upload_image(client)
    job_id = result["job_id"]

    version_id = _version_id(result["material"]["id"])
    parse_version_job(job_id)  # 第一次消息
    first = _chunks(version_id)
    assert first, "首次解析应当产生片段"

    parse_version_job(job_id)  # 重复消息（补偿重投/队列重复投递）
    assert _chunks(version_id) == first  # 片段数量与内容都不变

    job = _job_row(job_id)
    assert job.status == JobStatus.DONE
    assert job.attempts == 1  # 只执行了一次


def test_second_claim_is_rejected(client: TestClient, shared_storage) -> None:
    """幂等领取：只有一次 claim 能成功，其余消息领取失败。"""
    client.app.state.job_queue = RecordingQueue()
    job_id = _upload_image(client)["job_id"]

    session = _new_session()
    try:
        repo = SqlAlchemyJobRepository(session)
        assert repo.claim(job_id) is True
        session.commit()
        assert repo.claim(job_id) is False
    finally:
        session.close()


def test_claim_rejects_unknown_job(client: TestClient) -> None:
    """未提交/已清理的作业不能领取（J003 验收在 J004 领取路径上继续成立）。"""
    session = _new_session()
    try:
        assert SqlAlchemyJobRepository(session).claim(999999) is False
    finally:
        session.close()


def test_compensation_scan_redelivers_after_interruption(
    client: TestClient, shared_storage
) -> None:
    """投递中断后可恢复：启动补偿扫描把待投递记录重新投出（J004 验收）。"""
    client.app.state.job_queue = BrokenQueue()
    job_id = _upload_image(client)["job_id"]
    assert _job_row(job_id).enqueued_at is None

    queue = RecordingQueue()
    client.app.state.job_queue = queue
    delivered = compensate_pending_deliveries(client.app)

    assert delivered == 1
    assert queue.enqueued == [job_id]
    assert _job_row(job_id).enqueued_at is not None

    # 恢复后 Worker 真正把它跑完
    engine = create_engine(TEST_DATABASE_URL)
    fake_redis = fakeredis.FakeStrictRedis()
    rq_queue = Queue(QUEUE_NAME, connection=fake_redis)
    rq_queue.enqueue(parse_version_job, job_id, job_id=rq_job_key(job_id))
    SimpleWorker([rq_queue], connection=fake_redis).work(burst=True)

    session = sessionmaker(bind=engine)()
    try:
        material = SqlAlchemyMaterialRepository(session).get(_material_id_of(job_id))
        assert material.status == MaterialStatus.DONE
    finally:
        session.close()


def test_compensation_is_best_effort_when_redis_still_down(
    client: TestClient, shared_storage
) -> None:
    """Redis 仍不可用时补偿不抛异常、不阻塞启动，记录保持待投递。"""
    client.app.state.job_queue = BrokenQueue()
    job_id = _upload_image(client)["job_id"]

    assert compensate_pending_deliveries(client.app) == 0
    assert _job_row(job_id).enqueued_at is None


def test_compensation_disabled_by_setting(client: TestClient, shared_storage, monkeypatch) -> None:
    """关闭启动补偿时只保留手动入口：扫描不动作。"""
    client.app.state.job_queue = BrokenQueue()
    job_id = _upload_image(client)["job_id"]

    monkeypatch.setattr(client.app.state.settings, "dispatch_on_startup", False)
    queue = RecordingQueue()
    client.app.state.job_queue = queue

    assert compensate_pending_deliveries(client.app) == 0
    assert queue.enqueued == []
    assert _job_row(job_id).enqueued_at is None


def test_queue_uses_stable_job_key() -> None:
    """稳定作业键：同一条待投递记录重复投递复用一个队列标识（J004 交付物）。"""
    fake_redis = fakeredis.FakeStrictRedis()
    rq_queue = RQJobQueue("redis://127.0.0.1:6379/0", connection=fake_redis)

    assert rq_queue.enqueue_parse(7) == rq_job_key(7)
    assert rq_queue.enqueue_parse(7) == "parse-7"
    assert rq_job_key(7) == "parse-7"


def _version_id(material_id: int) -> int:
    session = _new_session()
    try:
        return SqlAlchemyMaterialRepository(session).list_versions(material_id)[0].id
    finally:
        session.close()


def _material_id_of(job_id: int) -> int:
    return _job_row(job_id).material_id
