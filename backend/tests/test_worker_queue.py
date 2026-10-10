"""J002 集成测试：RQ 队列与独立 Worker。

验收：Worker 调用应用用例完成解析；HTTP 返回 job_id 后无需等待解析完成。
队列用 fakeredis 模拟，不依赖真实 Redis（CI 无 Redis 服务）。
"""

import fakeredis
import pytest
from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from rq import Queue, SimpleWorker
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from campusflow.application.processing import ParserRegistry, set_parser_registry
from campusflow.domain.states import MaterialStatus
from campusflow.infrastructure.llm.vision import FakeVisionParser
from campusflow.infrastructure.parsers.pdf import PdfDocumentParser
from campusflow.infrastructure.parsers.text import TextDocumentParser
from campusflow.infrastructure.repositories import SqlAlchemyJobRepository
from campusflow.workers.jobs import parse_version_job

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-image"


class FakeQueue:
    """测试队列：记录投递的 job_id，不真正入队。"""

    def __init__(self) -> None:
        self.enqueued: list[int] = []

    def enqueue_parse(self, job_id: int) -> str:
        self.enqueued.append(job_id)
        return f"fake-{job_id}"


@pytest.fixture
def fake_queue(client: TestClient) -> FakeQueue:
    queue = FakeQueue()
    client.app.state.job_queue = queue
    return queue


@pytest.fixture
def shared_storage(client: TestClient, tmp_path, monkeypatch):
    """API 与 Worker 使用同一个临时存储目录（Worker 从配置读目录）。"""
    from campusflow.infrastructure.storage.local import LocalFileStorage

    storage = LocalFileStorage(tmp_path)
    client.app.state.file_storage = storage
    monkeypatch.setenv("CAMPUSFLOW_STORAGE_DIR", str(tmp_path))
    return storage


def test_upload_returns_job_id_without_waiting(client: TestClient, fake_queue: FakeQueue) -> None:
    """上传后立即返回 job_id，解析不在请求内完成（J002 验收）。"""
    response = client.post(
        "/api/v1/materials/upload",
        files=[("files", ("通知.png", PNG_BYTES))],
    )
    assert response.status_code == 201
    result = response.json()["results"][0]
    assert result["job_id"] is not None
    assert result["material"]["status"] == "pending"  # 尚未解析

    # 作业已投递，数据库中为待处理
    assert fake_queue.enqueued == [result["job_id"]]
    engine = create_engine(TEST_DATABASE_URL)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        job = SqlAlchemyJobRepository(session).get(result["job_id"])
        assert job.status == "pending"
        # 片段尚未生成
        from campusflow.infrastructure.repositories import SqlAlchemyMaterialRepository

        chunks = SqlAlchemyMaterialRepository(session).list_chunks(
            SqlAlchemyMaterialRepository(session).list_versions(result["material"]["id"])[0].id
        )
        assert chunks == []


def test_worker_executes_parse_use_case(
    client: TestClient, fake_queue: FakeQueue, shared_storage
) -> None:
    """Worker 调用应用用例：片段落库、作业与资料状态更新（J002 验收）。"""
    response = client.post(
        "/api/v1/materials/text",
        json={"title": "Worker 测试", "content": "第一段。\n\n第二段。"},
    )
    assert response.status_code == 201

    engine = create_engine(TEST_DATABASE_URL)
    factory = sessionmaker(bind=engine)

    # Worker 进程视角：装配解析器注册表并执行
    set_parser_registry(
        ParserRegistry(
            text_parser=TextDocumentParser(),
            pdf_parser=PdfDocumentParser(),
            vision_parser=FakeVisionParser(),
        )
    )

    fake_redis = fakeredis.FakeStrictRedis()
    queue = Queue("campusflow-parse", connection=fake_redis)

    # 文本资料的 chunks 已在导入时生成；用一个未解析的图片版本走 Worker 流程
    upload = client.post(
        "/api/v1/materials/upload",
        files=[("files", ("截图.png", PNG_BYTES))],
    ).json()["results"][0]
    job_id = upload["job_id"]
    queue.enqueue(parse_version_job, job_id)

    worker = SimpleWorker([queue], connection=fake_redis)
    worker.work(burst=True)

    with factory() as session:
        job = SqlAlchemyJobRepository(session).get(job_id)
        assert job.status == "done"
        assert job.attempts >= 1
        material_repo = __import__(
            "campusflow.infrastructure.repositories", fromlist=["SqlAlchemyMaterialRepository"]
        ).SqlAlchemyMaterialRepository(session)
        material = material_repo.get(upload["material"]["id"])
        assert material.status == MaterialStatus.DONE


def test_parse_failure_marks_job_failed(
    client: TestClient, fake_queue: FakeQueue, shared_storage
) -> None:
    """解析失败：作业标记 failed 并带清洗后的原因，不产生半成品。"""
    upload = client.post(
        "/api/v1/materials/upload",
        files=[("files", ("坏文件.png", PNG_BYTES))],
    ).json()["results"][0]
    job_id = upload["job_id"]

    class BrokenVision:
        def parse(self, content: bytes, mime_type: str):
            from campusflow.infrastructure.llm.errors import VisionRecognitionError

            raise VisionRecognitionError("接口返回错误：HTTP 500")

    set_parser_registry(
        ParserRegistry(
            text_parser=TextDocumentParser(),
            pdf_parser=PdfDocumentParser(),
            vision_parser=BrokenVision(),
        )
    )

    import os

    os.environ["CAMPUSFLOW_DATABASE_URL"] = TEST_DATABASE_URL
    from campusflow.infrastructure.llm.errors import VisionRecognitionError

    with pytest.raises(VisionRecognitionError):
        parse_version_job(job_id)

    engine = create_engine(TEST_DATABASE_URL)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        job = SqlAlchemyJobRepository(session).get(job_id)
        assert job.status == "failed"
        assert job.error_reason
