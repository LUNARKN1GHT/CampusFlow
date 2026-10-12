"""J008 集成测试：队列断连、重复消息与部分失败恢复（故障注入）。

验收：
- 部分失败可见且成功页保留；
- 恢复后结果无重复且删除竞态不回写。

三类中断分别注入：Redis（投递失败）、数据库（写入时数据库断连）、
Worker（进程被强杀，作业停在处理中）。恢复路径统一走：
超时回收 / 补偿扫描 → 重新投递 → 幂等领取 → 只补未完成的部分。
"""

import pytest
from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from campusflow.application.jobs import reclaim_stale_jobs
from campusflow.application.ports.parsers import (
    ParsedFragment,
    ParseFailure,
    ParseOutcome,
    ParserLocator,
)
from campusflow.application.processing import ParserRegistry, set_parser_registry
from campusflow.domain.jobs import timeout_reason
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
from campusflow.main import compensate_pending_deliveries
from campusflow.workers.jobs import parse_version_job

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fault-injection-image"

DB_OUTAGE = "server closed the connection unexpectedly（故障注入：数据库断连）"


class RecordingQueue:
    """正常投递：只记录，不连接 Redis。"""

    def __init__(self) -> None:
        self.enqueued: list[int] = []

    def enqueue_parse(self, job_id: int) -> str:
        self.enqueued.append(job_id)
        return f"parse-{job_id}"


class BrokenQueue:
    """Redis 断连：投递抛异常（与 RQ 连接失败一致）。"""

    def enqueue_parse(self, job_id: int) -> str:
        raise ConnectionError("redis 不可用")


class StagedRecoveringParser:
    """分阶段失败后恢复：第 1 页始终成功，第 2 页第一次临时失败。"""

    def __init__(self) -> None:
        self.page2_ok = False
        self.calls = 0

    def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
        self.calls += 1
        fragments = [
            ParsedFragment(
                seq=0, locator=ParserLocator(kind="page", page=1), text="第 1 页识别成功的内容"
            )
        ]
        if self.page2_ok:
            fragments.append(
                ParsedFragment(
                    seq=1,
                    locator=ParserLocator(kind="page", page=2),
                    text="第 2 页恢复后识别出的内容",
                )
            )
            return ParseOutcome(fragments=fragments, failures=[])
        return ParseOutcome(
            fragments=fragments,
            failures=[
                ParseFailure(
                    locator=ParserLocator(kind="page", page=2),
                    reason="第 2 页识别失败：上游 503",
                    retryable=True,
                )
            ],
        )


class KillingParser:
    """Worker 被强杀：解析中途进程没了（BaseException 不会进失败处理路径）。"""

    def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
        raise SystemExit("故障注入：Worker 被强杀")


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
    """测试不依赖真实 Redis：默认投递走记录队列，结束后还原。"""
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
    """资料当前全部版本的片段（按版本、序号排序）。"""
    session = _new_session()
    try:
        repo = SqlAlchemyMaterialRepository(session)
        chunks = []
        for version in repo.list_versions(material_id):
            chunks.extend(repo.list_chunks(version.id))
        return chunks
    finally:
        session.close()


def _material_status(material_id: int) -> str | None:
    session = _new_session()
    try:
        material = SqlAlchemyMaterialRepository(session).get(material_id)
        return material.status if material else None
    finally:
        session.close()


def _retry_via_api(client: TestClient, job_id: int) -> dict:
    response = client.post(f"/api/v1/jobs/{job_id}/retry")
    assert response.status_code == 200, response.text
    return response.json()


def _job_status_via_api(client: TestClient, job_id: int) -> dict:
    response = client.get(f"/api/v1/jobs/{job_id}")
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------------------ 数据库中断


def test_database_outage_mid_parse_leaves_no_partial_result(
    client: TestClient, shared_storage, monkeypatch
) -> None:
    """数据库在写入片段时断连：不留半成品，数据库恢复后能可重试地跑完。

    注入三处同时不可用（片段写入、失败记录、资料状态），与"连接断了"一致：
    Worker 这一次什么都写不进去，作业停在处理中；数据库恢复后由超时回收
    标记为可重试失败，重投后完成且片段只有一份。
    """
    _use_vision_parser(FakeVisionParser())
    result = _upload_image(client, tag=b"-db-outage")
    material_id = result["material"]["id"]
    job_id = result["job_id"]

    # 用一个可开关的挡板，而不是 monkeypatch.undo()：undo 会连带撤掉存储目录
    # 环境变量，Worker 就找不到原文件了，测的就不再是"数据库恢复"这一件事
    down = {"active": True}

    def _fail_when_down(original):
        def wrapper(*args, **kwargs):
            if down["active"]:
                raise OperationalError(DB_OUTAGE, None, Exception(DB_OUTAGE))
            return original(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(
        SqlAlchemyMaterialRepository,
        "add_chunks",
        _fail_when_down(SqlAlchemyMaterialRepository.add_chunks),
    )
    monkeypatch.setattr(
        SqlAlchemyJobRepository,
        "record_attempt",
        _fail_when_down(SqlAlchemyJobRepository.record_attempt),
    )
    monkeypatch.setattr(
        SqlAlchemyMaterialRepository,
        "set_status",
        _fail_when_down(SqlAlchemyMaterialRepository.set_status),
    )

    with pytest.raises(OperationalError):
        parse_version_job(job_id)

    # 半成品不留：没有片段、没有尝试历史，作业停在处理中、资料也没被改坏
    assert _chunks(material_id) == []
    assert _attempts(job_id) == []
    assert _job(job_id).status == JobStatus.RUNNING
    assert _material_status(material_id) == MaterialStatus.PENDING

    # 数据库恢复：超时回收把卡住的作业标为可重试失败
    down["active"] = False
    session = _new_session()
    try:
        reclaimed = reclaim_stale_jobs(build_repositories(session), timeout_seconds=0)
    finally:
        session.close()
    assert [job.id for job in reclaimed] == [job_id]

    job = _job(job_id)
    assert job.status == JobStatus.FAILED
    assert job.retryable is True
    assert job.error_reason == timeout_reason(0)
    assert _material_status(material_id) == MaterialStatus.FAILED

    # 重投 + 重跑：结果只写一份
    _retry_via_api(client, job_id)
    parse_version_job(job_id)

    assert _job(job_id).status == JobStatus.DONE
    assert [chunk.text for chunk in _chunks(material_id)] == ["假识别结果"]
    assert _material_status(material_id) == MaterialStatus.DONE


# -------------------------------------------------------------------- 队列中断


def test_redis_outage_during_retry_recovers_without_duplicates(
    client: TestClient, shared_storage
) -> None:
    """Redis 断连时的重投不丢：作业留在待投递，补偿扫描重投后无重复（J008 验收）。

    走完整恢复链：部分失败 → 重试（此时 Redis 不可用）→ 补偿扫描重投 →
    Worker 重跑，已成功页面的片段保持一份，未识别页面补齐。
    """
    parser = StagedRecoveringParser()
    _use_vision_parser(parser)
    result = _upload_image(client, tag=b"-redis-outage")
    material_id = result["material"]["id"]
    job_id = result["job_id"]

    parse_version_job(job_id)  # 第 2 页临时失败
    first_chunks = _chunks(material_id)
    assert [chunk.text for chunk in first_chunks] == ["第 1 页识别成功的内容"]

    parser.page2_ok = True  # 上游恢复

    # 重试时 Redis 仍不可用：重置为待处理，但投递不出去
    client.app.state.job_queue = BrokenQueue()
    retried = _retry_via_api(client, job_id)
    assert retried["status"] == JobStatus.PENDING
    assert retried["enqueued_at"] is None  # 还没投出去，等补偿
    assert retried["is_terminal"] is False

    # Redis 恢复：补偿扫描重投
    queue = RecordingQueue()
    client.app.state.job_queue = queue
    assert compensate_pending_deliveries(client.app) == 1
    assert queue.enqueued == [job_id]

    parse_version_job(job_id)
    final_chunks = _chunks(material_id)
    assert [chunk.seq for chunk in final_chunks] == [0, 1]
    assert [chunk.text for chunk in final_chunks] == [
        "第 1 页识别成功的内容",
        "第 2 页恢复后识别出的内容",
    ]
    assert _job(job_id).status == JobStatus.DONE
    assert _material_status(material_id) == MaterialStatus.DONE


# ------------------------------------------------------- 部分失败可见且页面保留


def test_partial_failure_is_visible_with_successful_pages_kept(
    client: TestClient, shared_storage
) -> None:
    """部分失败：成功页面的片段保留，未识别范围按页列出且可重试（J008 验收）。"""
    _use_vision_parser(StagedRecoveringParser())
    result = _upload_image(client, tag=b"-partial")
    material_id = result["material"]["id"]
    job_id = result["job_id"]

    parse_version_job(job_id)

    status = _job_status_via_api(client, job_id)
    assert status["status"] == JobStatus.PARTIAL
    assert status["is_terminal"] is True  # 部分成功也是终态，前端可以停轮询
    assert status["retryable"] is True  # 失败范围是临时故障，值得只补那一页
    assert "完成 1 个片段" in status["scope"]
    assert "第 2 页" in status["scope"]
    assert "第 1 页识别成功的内容" in [chunk.text for chunk in _chunks(material_id)]

    attempt = _attempts(job_id)[0]
    assert attempt.status == JobStatus.PARTIAL
    assert attempt.retryable is True
    assert "第 2 页" in attempt.scope


def test_duplicate_message_after_partial_failure_is_ignored(
    client: TestClient, shared_storage
) -> None:
    """部分失败后队列重复投递同一条消息：幂等领取挡下，不重复解析（J008 验收）。"""
    parser = StagedRecoveringParser()
    _use_vision_parser(parser)
    result = _upload_image(client, tag=b"-duplicate-partial")
    material_id = result["material"]["id"]
    job_id = result["job_id"]

    parse_version_job(job_id)
    first_chunks = _chunks(material_id)
    assert _job(job_id).status == JobStatus.PARTIAL

    parser.page2_ok = True  # 上游已恢复，重复消息也不该偷偷再跑一遍
    parse_version_job(job_id)

    assert _chunks(material_id) == first_chunks
    job = _job(job_id)
    assert job.status == JobStatus.PARTIAL
    assert job.attempts == 1  # 只有一次执行
    assert len(_attempts(job_id)) == 1


# ------------------------------------------------------------------- Worker 强杀


def test_killed_worker_leaves_no_partial_result_and_is_reclaimable(
    client: TestClient, shared_storage
) -> None:
    """Worker 解析中途被强杀：作业停在处理中，不留半成品，回收后可重跑（J008 验收）。"""
    _use_vision_parser(KillingParser())
    result = _upload_image(client, tag=b"-killed")
    material_id = result["material"]["id"]
    job_id = result["job_id"]

    with pytest.raises(SystemExit):
        parse_version_job(job_id)

    # 进程没了：没有片段、没有终态记录，作业停在处理中（不是"完成"）
    assert _chunks(material_id) == []
    assert _attempts(job_id) == []
    assert _job(job_id).status == JobStatus.RUNNING
    assert _material_status(material_id) == MaterialStatus.PENDING

    # 恢复：超时回收 → 重新投递（队列里还躺着那条没跑完的消息）→ 重跑
    session = _new_session()
    try:
        assert len(reclaim_stale_jobs(build_repositories(session), timeout_seconds=0)) == 1
    finally:
        session.close()

    _use_vision_parser(FakeVisionParser())
    parse_version_job(job_id)  # 队列里那条旧消息：领取失败，不重复解析
    assert _chunks(material_id) == []

    _retry_via_api(client, job_id)
    parse_version_job(job_id)

    assert _job(job_id).status == JobStatus.DONE
    assert [chunk.text for chunk in _chunks(material_id)] == ["假识别结果"]
    assert len(_attempts(job_id)) == 2  # 超时失败 + 重跑成功，各一条


# ------------------------------------------------------------- 删除竞态（恢复路径）


def test_retry_after_delete_does_not_resurrect_material(client: TestClient, shared_storage) -> None:
    """恢复路径上的删除竞态：资料删掉后，重投与重试都不写回（J008 验收）。"""
    _use_vision_parser(StagedRecoveringParser())
    result = _upload_image(client, tag=b"-delete-race")
    material_id = result["material"]["id"]
    job_id = result["job_id"]

    parse_version_job(job_id)
    assert _job(job_id).status == JobStatus.PARTIAL

    # 用户在重试之前把资料删了
    impact = client.get(f"/api/v1/materials/{material_id}/delete-impact")
    assert impact.status_code == 200, impact.text
    response = client.post(
        f"/api/v1/materials/{material_id}/delete",
        json={"confirm_token": impact.json()["confirm_token"], "keep_tasks": True},
    )
    assert response.status_code == 204, response.text

    # 队列里那条迟到的消息：领取失败，什么都不写
    parse_version_job(job_id)
    # 手动重试：作业已随资料删除，按"作业不存在"处理
    assert client.post(f"/api/v1/jobs/{job_id}/retry").status_code == 404
    assert client.get(f"/api/v1/jobs/{job_id}").status_code == 404

    for model in (Material, ProcessingJob):
        session = _new_session()
        try:
            assert session.get(model, material_id if model is Material else job_id) is None
        finally:
            session.close()
    assert _chunks(material_id) == []
