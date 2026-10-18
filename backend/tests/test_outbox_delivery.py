"""J003 集成测试：提交与投递分离（Outbox 待投递记录）。

验收：
- Redis 不可用时资料和待投递记录不丢；
- 未提交资料不能被 Worker 处理（消息只在提交后产生）。
"""

import pytest
from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from campusflow.application.dispatch import dispatch_pending_jobs
from campusflow.application.ports.repositories import ProcessingJobData, Repositories
from campusflow.application.processing import parse_version
from campusflow.infrastructure.repositories import (
    SqlAlchemyJobRepository,
    SqlAlchemyMaterialRepository,
    build_repositories,
)
from campusflow.infrastructure.storage.local import LocalFileStorage

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"outbox-image"


def _new_session() -> Session:
    return sessionmaker(bind=create_engine(TEST_DATABASE_URL))()


def _repos(session: Session) -> Repositories:
    return build_repositories(session)


class BrokenQueue:
    """模拟 Redis 不可用：投递即抛连接错误。"""

    def enqueue_parse(self, job_id: int) -> str:
        raise ConnectionError("redis 不可用")


class RecordingQueue:
    """正常队列：记录投递的 job_id。"""

    def __init__(self) -> None:
        self.enqueued: list[int] = []

    def enqueue_parse(self, job_id: int) -> str:
        self.enqueued.append(job_id)
        return f"q-{job_id}"


class CommitCheckingQueue:
    """投递时用独立连接核对：此刻作业必须已在数据库中提交可见。"""

    def __init__(self) -> None:
        self.enqueued: list[int] = []
        self.seen_committed: list[bool] = []

    def enqueue_parse(self, job_id: int) -> str:
        session = _new_session()
        try:
            visible = SqlAlchemyJobRepository(session).get(job_id) is not None
        finally:
            session.close()
        self.seen_committed.append(visible)
        self.enqueued.append(job_id)
        return f"q-{job_id}"


@pytest.fixture
def shared_storage(client: TestClient, tmp_path):
    storage = LocalFileStorage(tmp_path)
    client.app.state.file_storage = storage
    return storage


def _upload(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/materials/upload",
        files=[("files", ("通知.png", PNG_BYTES))],
    )
    assert response.status_code == 201, response.text
    return response.json()["results"][0]


def _job(job_id: int) -> ProcessingJobData | None:
    session = _new_session()
    try:
        return SqlAlchemyJobRepository(session).get(job_id)
    finally:
        session.close()


def test_redis_unavailable_keeps_material_and_outbox_record(
    client: TestClient, shared_storage
) -> None:
    """Redis 不可用时：建档照常返回 job_id，资料与待投递记录都在库里（J003 验收）。"""
    client.app.state.job_queue = BrokenQueue()

    result = _upload(client)

    assert result["job_id"] is not None
    assert result["material"]["status"] == "pending"
    assert result["material"]["id"] is not None

    job = _job(result["job_id"])
    assert job is not None
    assert job.status == "pending"
    assert job.enqueued_at is None  # 仍是待投递记录
    assert job.delivery_attempts == 1  # 失败尝试已计数

    session = _new_session()
    try:
        undelivered = [j.id for j in SqlAlchemyJobRepository(session).list_undelivered()]
    finally:
        session.close()
    assert undelivered == [result["job_id"]]


def test_message_only_after_commit(client: TestClient, shared_storage) -> None:
    """投递时作业必须已提交可见——未提交资料不可能被 Worker 处理（J003 验收）。"""
    queue = CommitCheckingQueue()
    client.app.state.job_queue = queue

    result = _upload(client)

    assert queue.enqueued == [result["job_id"]]
    assert queue.seen_committed == [True]

    job = _job(result["job_id"])
    assert job.enqueued_at is not None
    assert job.delivery_attempts == 1


def test_compensation_redelivers_pending_job(client: TestClient, shared_storage) -> None:
    """投递中断后可恢复：补偿扫描把待投递记录重新投出（J004 的前置能力）。"""
    client.app.state.job_queue = BrokenQueue()
    result = _upload(client)
    assert _job(result["job_id"]).enqueued_at is None

    # Redis 恢复后，补偿扫描重投待投递记录
    queue = RecordingQueue()
    session = _new_session()
    try:
        outcome = dispatch_pending_jobs(_repos(session), queue)
    finally:
        session.close()

    assert outcome.delivered == 1
    assert outcome.failed == 0
    assert queue.enqueued == [result["job_id"]]

    job = _job(result["job_id"])
    assert job.enqueued_at is not None
    assert job.delivery_attempts == 2  # 一次失败、一次成功


def test_worker_skips_job_without_committed_row(tmp_path) -> None:
    """回滚/未提交资料对应的消息：Worker 静默跳过，不产生任何副作用（J003 验收）。"""
    session = _new_session()
    try:
        repos = _repos(session)
        # 999999 不存在（等价于事务已回滚、作业行从未提交）
        assert parse_version(repos, LocalFileStorage(tmp_path), 999999) == []
    finally:
        session.close()


def test_rolled_back_import_leaves_no_job_and_no_delivery(
    client: TestClient, shared_storage, monkeypatch
) -> None:
    """建档事务失败：不产生作业记录、不投递，更不会留下可被 Worker 处理的消息。"""
    queue = RecordingQueue()
    client.app.state.job_queue = queue

    def boom(*args, **kwargs):
        raise RuntimeError("模拟建档中途失败")

    monkeypatch.setattr(SqlAlchemyMaterialRepository, "create_version", boom, raising=True)

    with pytest.raises(RuntimeError):
        client.post("/api/v1/materials/upload", files=[("files", ("通知.png", PNG_BYTES))])

    assert queue.enqueued == []
    session = _new_session()
    try:
        assert SqlAlchemyJobRepository(session).list_undelivered() == []
    finally:
        session.close()
