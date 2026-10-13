"""J006 集成测试：作业状态查询与失败重试 API。

验收：
- 前端能轮询直到终态；
- 无权访问的 job_id 不泄露状态。
"""

import time

import pytest
from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from campusflow.application.ports.parsers import (
    ParsedFragment,
    ParseFailure,
    ParseOutcome,
    ParserLocator,
)
from campusflow.application.processing import ParserRegistry, set_parser_registry
from campusflow.domain.jobs import can_retry, is_terminal
from campusflow.domain.states import JobStatus, MaterialSourceType, MaterialStatus
from campusflow.infrastructure.db.models import Material, MaterialVersion, ProcessingJob, Workspace
from campusflow.infrastructure.llm.errors import VisionRecognitionError
from campusflow.infrastructure.llm.vision import FakeVisionParser
from campusflow.infrastructure.parsers.pdf import PdfDocumentParser
from campusflow.infrastructure.parsers.text import TextDocumentParser
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage
from campusflow.workers.jobs import parse_version_job

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"jobs-api-image"


class RecordingQueue:
    """只记录投递，不连接 Redis。"""

    def __init__(self) -> None:
        self.enqueued: list[int] = []

    def enqueue_parse(self, job_id: int) -> str:
        self.enqueued.append(job_id)
        return f"parse-{job_id}"


class TransientVisionParser:
    """整次调用失败：临时故障，可重试。"""

    def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
        raise VisionRecognitionError("视觉模型调用失败：上游 503")


class PermanentVisionParser:
    """格式不支持：永久失败，重试不会成功。"""

    def parse(self, content: bytes, mime_type: str) -> ParseOutcome:
        raise ValueError("图片格式不支持")


class StagedVisionParser:
    """分阶段失败：第 1 页成功、第 2 页临时失败。"""

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


def test_terminal_and_retryable_rules() -> None:
    """终态与可重试性的判定规则（J006 交付物）。"""
    assert is_terminal(JobStatus.DONE) is True
    assert is_terminal(JobStatus.PARTIAL) is True
    assert is_terminal(JobStatus.FAILED) is True
    assert is_terminal(JobStatus.PENDING) is False
    assert is_terminal(JobStatus.RUNNING) is False

    assert can_retry(JobStatus.FAILED, True) is True
    assert can_retry(JobStatus.PARTIAL, True) is True  # 只补没成功的阶段
    assert can_retry(JobStatus.FAILED, False) is False  # 永久失败不重投
    assert can_retry(JobStatus.FAILED, None) is False
    assert can_retry(JobStatus.DONE, None) is False
    assert can_retry(JobStatus.PENDING, True) is False  # 正在处理中不重复投递


@pytest.fixture
def shared_storage(client: TestClient, tmp_path, monkeypatch):
    storage = LocalFileStorage(tmp_path)
    client.app.state.file_storage = storage
    monkeypatch.setenv("CAMPUSFLOW_STORAGE_DIR", str(tmp_path))
    return storage


@pytest.fixture
def queue(client: TestClient):
    original = client.app.state.job_queue
    recorder = RecordingQueue()
    client.app.state.job_queue = recorder
    yield recorder
    client.app.state.job_queue = original


def _use_vision_parser(parser) -> None:
    set_parser_registry(
        ParserRegistry(
            text_parser=TextDocumentParser(),
            pdf_parser=PdfDocumentParser(),
            vision_parser=parser,
        )
    )


def _upload(client: TestClient, *, tag: bytes = b"") -> dict:
    """上传一份图片资料。tag 用于区分内容，避免同文件被判定为重复导入（D006）。"""
    response = client.post(
        "/api/v1/materials/upload",
        files=[("files", ("通知.png", PNG_BYTES + tag))],
    )
    assert response.status_code == 201, response.text
    result = response.json()["results"][0]
    assert result["job_id"] is not None, result
    return result


def _create_foreign_job() -> int:
    """在第二个空间直接建资料、版本与作业，模拟"猜 ID"访问他人作业。"""
    engine = create_engine(TEST_DATABASE_URL)
    with Session(engine) as session:
        other = Workspace(name="另一个空间")
        session.add(other)
        session.flush()
        material = Material(
            workspace_id=other.id,
            title="他人的资料",
            source_type=MaterialSourceType.IMAGE,
            status=MaterialStatus.PENDING,
        )
        session.add(material)
        session.flush()
        version = MaterialVersion(material_id=material.id, version_no=1, storage_key="secret/x.png")
        session.add(version)
        session.flush()
        job = ProcessingJob(
            workspace_id=other.id,
            material_id=material.id,
            version_id=version.id,
            stage="parse",
        )
        session.add(job)
        session.commit()
        return job.id


# ------------------------------------------------------------ 状态查询与轮询


def test_frontend_can_poll_until_terminal(client: TestClient, shared_storage, queue) -> None:
    """轮询到终态：处理中返回 is_terminal=false，完成后为 true（J006 验收）。"""
    _use_vision_parser(FakeVisionParser())
    job_id = _upload(client)["job_id"]

    pending = client.get(f"/api/v1/jobs/{job_id}").json()
    assert pending["status"] == JobStatus.PENDING
    assert pending["is_terminal"] is False
    assert pending["retryable"] is False  # 处理中不允许重投

    # 前端轮询循环：拿到终态就停下
    parse_version_job(job_id)
    deadline = time.monotonic() + 5
    body = {}
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/jobs/{job_id}").json()
        if body["is_terminal"]:
            break
        time.sleep(0.05)

    assert body["status"] == JobStatus.DONE
    assert body["is_terminal"] is True
    assert body["stage"] == "parse"
    assert body["scope"]  # 覆盖范围可见
    assert body["error_reason"] is None
    assert body["retryable"] is False
    assert body["material_id"] == _material_id_of(job_id)


def test_status_exposes_scope_and_retryability_of_partial_job(
    client: TestClient, shared_storage, queue
) -> None:
    """部分成功：覆盖范围列出未识别范围，且标记为可重试（J006 交付物）。"""
    _use_vision_parser(StagedVisionParser())
    job_id = _upload(client)["job_id"]
    parse_version_job(job_id)

    body = client.get(f"/api/v1/jobs/{job_id}").json()
    assert body["status"] == JobStatus.PARTIAL
    assert body["is_terminal"] is True
    assert body["retryable"] is True
    assert "未识别范围" in body["scope"]


def test_attempts_history_is_exposed(client: TestClient, shared_storage, queue) -> None:
    """尝试历史可查：每次终态尝试一条，含阶段、状态与原因（J005/J006）。"""
    _use_vision_parser(TransientVisionParser())
    job_id = _upload(client)["job_id"]
    with pytest.raises(VisionRecognitionError):
        parse_version_job(job_id)

    attempts = client.get(f"/api/v1/jobs/{job_id}/attempts")
    assert attempts.status_code == 200
    body = attempts.json()
    assert len(body) == 1
    assert body[0]["attempt_no"] == 1
    assert body[0]["stage"] == "parse"
    assert body[0]["status"] == JobStatus.FAILED
    assert body[0]["retryable"] is True
    assert body[0]["error_reason"]
    assert "created_at" in body[0]


# ---------------------------------------------------------------- 失败重试


def test_retry_resets_job_and_redelivers(client: TestClient, shared_storage, queue) -> None:
    """可重试失败：重试把作业重置为待处理并重新投递（J006 交付物）。"""
    _use_vision_parser(TransientVisionParser())
    result = _upload(client)
    job_id = result["job_id"]
    with pytest.raises(VisionRecognitionError):
        parse_version_job(job_id)

    response = client.post(f"/api/v1/jobs/{job_id}/retry")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == JobStatus.PENDING
    assert body["is_terminal"] is False
    assert body["retryable"] is False  # 已重置，等这轮结果
    assert body["error_reason"] is None
    # 一次是上传后的首次投递，一次是本次重试的重新投递
    assert queue.enqueued == [job_id, job_id]

    session = _new_session()
    try:
        material = build_repositories(session).materials.get(result["material"]["id"])
        assert material.status == MaterialStatus.PENDING
    finally:
        session.close()


def test_retry_rejects_done_job(client: TestClient, shared_storage, queue) -> None:
    """已完成的作业不允许重试（避免无意义重投）。"""
    _use_vision_parser(FakeVisionParser())
    job_id = _upload(client)["job_id"]
    parse_version_job(job_id)

    delivered_before = list(queue.enqueued)
    response = client.post(f"/api/v1/jobs/{job_id}/retry")
    assert response.status_code == 400
    assert "不可重试" in response.json()["detail"]
    assert queue.enqueued == delivered_before  # 没有新的投递


def test_retry_rejects_permanent_failure(client: TestClient, shared_storage, queue) -> None:
    """永久失败不允许重试，并给出原因（J005 规则在 API 层的体现）。"""
    _use_vision_parser(PermanentVisionParser())
    job_id = _upload(client)["job_id"]
    with pytest.raises(ValueError):
        parse_version_job(job_id)

    response = client.post(f"/api/v1/jobs/{job_id}/retry")
    assert response.status_code == 400
    assert "永久失败" in response.json()["detail"]


def test_redeliver_pending_jobs(client: TestClient, shared_storage) -> None:
    """手动重投入口：队列不可用时留下的待投递作业可一次重投（J004/J006）。"""

    class BrokenQueue:
        def enqueue_parse(self, job_id: int) -> str:
            raise ConnectionError("redis 不可用")

    client.app.state.job_queue = BrokenQueue()
    job_id = _upload(client)["job_id"]
    assert client.get(f"/api/v1/jobs/{job_id}").json()["enqueued_at"] is None

    recorder = RecordingQueue()
    client.app.state.job_queue = recorder
    response = client.post("/api/v1/jobs/redeliver")
    assert response.status_code == 200
    assert response.json() == {"delivered": 1, "failed": 0}
    assert recorder.enqueued == [job_id]
    assert client.get(f"/api/v1/jobs/{job_id}").json()["enqueued_at"] is not None

    # 队列整体不可用时接口不报错，只报告 0 条，记录仍留在待投递
    client.app.state.job_queue = None
    stuck_id = _upload(client, tag=b"-queueless")["job_id"]
    again = client.post("/api/v1/jobs/redeliver")
    assert again.json() == {"delivered": 0, "failed": 0}
    assert client.get(f"/api/v1/jobs/{stuck_id}").json()["enqueued_at"] is None


# ---------------------------------------------------------------- 空间权限


def test_foreign_job_status_is_not_leaked(client: TestClient, shared_storage) -> None:
    """无权访问的 job_id 不泄露状态：与不存在同样返回 404（J006 验收）。"""
    client.get("/api/v1/settings")  # 确保默认空间存在
    foreign_job_id = _create_foreign_job()

    detail = client.get(f"/api/v1/jobs/{foreign_job_id}")
    assert detail.status_code == 404
    assert detail.json() == {"detail": "作业不存在"}
    assert "他人" not in detail.text

    assert client.get(f"/api/v1/jobs/{foreign_job_id}/attempts").status_code == 404
    assert client.post(f"/api/v1/jobs/{foreign_job_id}/retry").status_code == 404


def test_unknown_job_returns_404(client: TestClient, shared_storage) -> None:
    """不存在的作业号与越权访问表现一致，无法据此区分。"""
    response = client.get("/api/v1/jobs/999999")
    assert response.status_code == 404
    assert response.json() == {"detail": "作业不存在"}


def test_redeliver_cannot_dispatch_another_workspace(client: TestClient, shared_storage, queue):
    client.get("/api/v1/settings")
    foreign_id = _create_foreign_job()
    response = client.post("/api/v1/jobs/redeliver")
    assert response.status_code == 200
    assert response.json() == {"delivered": 0, "failed": 0}
    assert queue.enqueued == []
    with _new_session() as session:
        job = build_repositories(session).jobs.get(foreign_id)
        assert job.enqueued_at is None


def _material_id_of(job_id: int) -> int:
    session = _new_session()
    try:
        return build_repositories(session).jobs.get(job_id).material_id
    finally:
        session.close()
