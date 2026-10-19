"""Q004 端到端验收：上传 → 解析 Worker → 自动建索引作业 → 索引 Worker →
向量落库 → 进度可查 → 删除资料后一并清除。

队列用 fakeredis 模拟；向量用确定性假模型，全程零费用。
"""

import fakeredis
import pytest
from fastapi.testclient import TestClient
from index_helpers import get_job, open_session
from rq import Queue, SimpleWorker
from sqlalchemy import func, select, text

from campusflow.application.processing import ParserRegistry, set_parser_registry
from campusflow.domain.states import JobStage, JobStatus
from campusflow.infrastructure.db.models import IndexedBlock, IndexManifest, SourceChunk
from campusflow.infrastructure.llm.vision import FakeVisionParser
from campusflow.infrastructure.parsers.pdf import PdfDocumentParser
from campusflow.infrastructure.parsers.text import TextDocumentParser
from campusflow.infrastructure.queue.rq_queue import INDEX_QUEUE_NAME, QUEUE_NAME
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage
from campusflow.workers.embeddings import build_demo_embedder, set_embedder_factory
from campusflow.workers.jobs import index_version_job, parse_version_job

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"e2e-image"


class NoopQueue:
    """上传时只记录，不连真实 Redis；投递由测试显式写入 fakeredis。"""

    def enqueue_parse(self, job_id: int) -> str:
        return f"parse-{job_id}"

    def enqueue_index(self, job_id: int) -> str:
        return f"index-{job_id}"


@pytest.fixture
def fake_queue(client: TestClient):
    original = client.app.state.job_queue
    client.app.state.job_queue = NoopQueue()
    yield
    client.app.state.job_queue = original


@pytest.fixture
def shared_storage(client: TestClient, tmp_path, monkeypatch):
    storage = LocalFileStorage(tmp_path)
    client.app.state.file_storage = storage
    monkeypatch.setenv("CAMPUSFLOW_STORAGE_DIR", str(tmp_path))
    return storage


@pytest.fixture(autouse=True)
def demo_embedder():
    set_embedder_factory(build_demo_embedder)
    yield
    set_embedder_factory(None)


def _run_worker(queue_name: str, redis_connection) -> None:
    queue = Queue(queue_name, connection=redis_connection)
    SimpleWorker([queue], connection=redis_connection).work(burst=True)


def test_upload_parse_index_progress_and_delete(client, shared_storage, fake_queue):
    set_parser_registry(
        ParserRegistry(
            text_parser=TextDocumentParser(),
            pdf_parser=PdfDocumentParser(),
            vision_parser=FakeVisionParser(),
        )
    )
    upload = client.post(
        "/api/v1/materials/upload", files=[("files", ("整份资料.png", PNG_BYTES))]
    ).json()["results"][0]
    material_id, parse_job_id = upload["material"]["id"], upload["job_id"]

    redis = fakeredis.FakeStrictRedis()
    Queue(QUEUE_NAME, connection=redis).enqueue(parse_version_job, parse_job_id)
    _run_worker(QUEUE_NAME, redis)

    assert get_job(parse_job_id).status == JobStatus.DONE
    with open_session() as session:
        jobs = build_repositories(session).jobs.list_by_material(material_id)
        embed_jobs = [job for job in jobs if job.stage == JobStage.EMBED]
        assert len(embed_jobs) == 1
        embed_job_id = embed_jobs[0].id
        version_id = embed_jobs[0].version_id
        chunk_count = session.scalar(
            select(func.count())
            .select_from(SourceChunk)
            .where(SourceChunk.version_id == version_id)
        )
    assert chunk_count > 0

    # 解析完成后作业仍是待处理：等后端恢复扫描投递（Q004 outbox 语义）
    assert get_job(embed_job_id).status == JobStatus.PENDING

    Queue(INDEX_QUEUE_NAME, connection=redis).enqueue(index_version_job, embed_job_id)
    _run_worker(INDEX_QUEUE_NAME, redis)

    embed_job = get_job(embed_job_id)
    assert embed_job.status == JobStatus.DONE
    with open_session() as session:
        done = session.scalar(
            select(func.count())
            .select_from(IndexedBlock)
            .where(IndexedBlock.version_id == version_id, IndexedBlock.state == "done")
        )
        assert done == chunk_count

    progress = client.get(f"/api/v1/jobs/{embed_job_id}/index-progress").json()
    assert progress["manifest_id"] is not None
    assert progress["summary"]["status"] == "done"
    assert progress["summary"]["fully_searchable"] is True
    assert progress["summary"]["indexed"] == chunk_count

    blocks = client.get(
        f"/api/v1/jobs/{embed_job_id}/index-progress", params={"include_blocks": True}
    ).json()["blocks"]
    assert len(blocks) == chunk_count
    assert all(block["state"] == "done" for block in blocks)

    # 删除资料：作业、清单、块一并清除，进度接口不再可达
    impact = client.get(f"/api/v1/materials/{material_id}/delete-impact").json()
    response = client.post(
        f"/api/v1/materials/{material_id}/delete",
        json={"confirm_token": impact["confirm_token"], "keep_tasks": True},
    )
    assert response.status_code == 204

    assert client.get(f"/api/v1/jobs/{embed_job_id}/index-progress").status_code == 404
    assert get_job(embed_job_id) is None
    with open_session() as session:
        assert session.scalar(select(func.count()).select_from(IndexedBlock)) == 0
        assert session.scalar(select(func.count()).select_from(IndexManifest)) == 0


def test_indexing_worker_handles_duplicate_queue_message(client, shared_storage, fake_queue):
    set_parser_registry(
        ParserRegistry(
            text_parser=TextDocumentParser(),
            pdf_parser=PdfDocumentParser(),
            vision_parser=FakeVisionParser(),
        )
    )
    upload = client.post(
        "/api/v1/materials/upload", files=[("files", ("重复消息.png", PNG_BYTES + b"-dup"))]
    ).json()["results"][0]
    material_id, parse_job_id = upload["material"]["id"], upload["job_id"]

    redis = fakeredis.FakeStrictRedis()
    Queue(QUEUE_NAME, connection=redis).enqueue(parse_version_job, parse_job_id)
    _run_worker(QUEUE_NAME, redis)

    with open_session() as session:
        embed_job_id = next(
            job.id
            for job in build_repositories(session).jobs.list_by_material(material_id)
            if job.stage == JobStage.EMBED
        )
        version_id = build_repositories(session).jobs.get(embed_job_id).version_id

    queue = Queue(INDEX_QUEUE_NAME, connection=redis)
    queue.enqueue(index_version_job, embed_job_id, job_id="index-dup")
    _run_worker(INDEX_QUEUE_NAME, redis)
    first_calls = _embedding_count(version_id)

    # 同一作业再投一次：领取失败，不重复写入、不重复调用模型
    queue.enqueue(index_version_job, embed_job_id, job_id="index-dup-again")
    _run_worker(INDEX_QUEUE_NAME, redis)

    assert _embedding_count(version_id) == first_calls
    assert get_job(embed_job_id).status == JobStatus.DONE


def _embedding_count(version_id: int) -> int:
    with open_session() as session:
        return int(
            session.scalar(
                text(
                    "SELECT count(*) FROM indexed_blocks "
                    "WHERE version_id = :version_id AND embedding IS NOT NULL"
                ),
                {"version_id": version_id},
            )
        )
