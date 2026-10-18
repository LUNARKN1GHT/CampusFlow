"""Q004 后台链路：解析成功自动建索引作业、按阶段投递、重复消息无害。"""

import pytest
from fastapi.testclient import TestClient
from index_helpers import create_source, get_job, open_session, workspace_id

from campusflow.application.dispatch import dispatch_job, dispatch_pending_jobs
from campusflow.application.processing import ParserRegistry, set_parser_registry
from campusflow.domain.states import JobStage, JobStatus
from campusflow.infrastructure.db.models import ProcessingJob
from campusflow.infrastructure.llm.vision import FakeVisionParser
from campusflow.infrastructure.parsers.pdf import PdfDocumentParser
from campusflow.infrastructure.parsers.text import TextDocumentParser
from campusflow.infrastructure.queue.rq_queue import rq_index_job_key, rq_job_key
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage
from campusflow.workers.jobs import parse_version_job

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"pipeline-image"


class RecordingQueue:
    def __init__(self) -> None:
        self.parse_calls: list[int] = []
        self.index_calls: list[int] = []

    def enqueue_parse(self, job_id: int) -> str:
        self.parse_calls.append(job_id)
        return rq_job_key(job_id)

    def enqueue_index(self, job_id: int) -> str:
        self.index_calls.append(job_id)
        return rq_index_job_key(job_id)


class BrokenQueue:
    def enqueue_parse(self, job_id: int) -> str:
        raise ConnectionError("redis 不可用")

    def enqueue_index(self, job_id: int) -> str:
        raise ConnectionError("redis 不可用")


@pytest.fixture
def storage(client, tmp_path, monkeypatch):
    store = LocalFileStorage(tmp_path)
    client.app.state.file_storage = store
    monkeypatch.setenv("CAMPUSFLOW_STORAGE_DIR", str(tmp_path))
    yield store


@pytest.fixture(autouse=True)
def local_queue(client):
    original = client.app.state.job_queue
    recorder = RecordingQueue()
    client.app.state.job_queue = recorder
    yield recorder
    client.app.state.job_queue = original


def _use_vision(parser) -> None:
    set_parser_registry(
        ParserRegistry(
            text_parser=TextDocumentParser(),
            pdf_parser=PdfDocumentParser(),
            vision_parser=parser,
        )
    )


def _upload(client: TestClient, tag: bytes = b"") -> int:
    response = client.post(
        "/api/v1/materials/upload", files=[("files", ("资料.png", PNG_BYTES + tag))]
    )
    assert response.status_code == 201, response.text
    return response.json()["results"][0]["job_id"]


def _jobs_of(job_id: int) -> list:
    with open_session() as session:
        repos = build_repositories(session)
        return repos.jobs.list_by_material(repos.jobs.get(job_id).material_id)


def test_parse_success_creates_exactly_one_embed_job(client, storage):
    _use_vision(FakeVisionParser())
    job_id = _upload(client)
    parse_version_job(job_id)

    jobs = _jobs_of(job_id)
    parse_jobs = [job for job in jobs if job.stage == JobStage.PARSE]
    embed_jobs = [job for job in jobs if job.stage == JobStage.EMBED]
    assert len(parse_jobs) == 1 and parse_jobs[0].status == JobStatus.DONE
    assert len(embed_jobs) == 1
    assert embed_jobs[0].status == JobStatus.PENDING
    assert embed_jobs[0].version_id == parse_jobs[0].version_id

    # 重复消息不会造出第二个索引作业，也不会重置正在等待的作业
    parse_version_job(job_id)
    assert len([job for job in _jobs_of(job_id) if job.stage == JobStage.EMBED]) == 1


def test_duplicate_parse_message_keeps_single_embed_job(client, storage):
    _use_vision(FakeVisionParser())
    job_id = _upload(client)
    parse_version_job(job_id)
    parse_version_job(job_id)

    embed_jobs = [job for job in _jobs_of(job_id) if job.stage == JobStage.EMBED]
    assert len(embed_jobs) == 1
    assert embed_jobs[0].status == JobStatus.PENDING


def test_failed_parse_does_not_create_embed_job(client, storage):
    class FailingParser:
        def parse(self, content: bytes, mime_type: str):
            from campusflow.application.ports.parsers import (
                ParseFailure,
                ParseOutcome,
                ParserLocator,
            )

            return ParseOutcome(
                fragments=[],
                failures=[
                    ParseFailure(
                        locator=ParserLocator(kind="page", page=1),
                        reason="整页无法识别",
                        retryable=False,
                    )
                ],
            )

    _use_vision(FailingParser())
    job_id = _upload(client, tag=b"-fail")
    parse_version_job(job_id)

    assert _jobs_of(job_id)[0].status == JobStatus.FAILED
    assert [job for job in _jobs_of(job_id) if job.stage == JobStage.EMBED] == []


def test_parse_retry_does_not_add_another_embed_job(client, storage):
    from campusflow.application.ports.parsers import (
        ParsedFragment,
        ParseFailure,
        ParseOutcome,
        ParserLocator,
    )

    class RecoveringParser:
        def parse(self, content: bytes, mime_type: str):
            return ParseOutcome(
                fragments=[
                    ParsedFragment(
                        seq=0,
                        locator=ParserLocator(kind="page", page=1),
                        text="第 1 页内容",
                    )
                ],
                failures=[
                    ParseFailure(
                        locator=ParserLocator(kind="page", page=2),
                        reason="第 2 页临时失败",
                        retryable=True,
                    )
                ],
            )

    _use_vision(RecoveringParser())
    job_id = _upload(client, tag=b"-retry")
    parse_version_job(job_id)
    assert len([job for job in _jobs_of(job_id) if job.stage == JobStage.EMBED]) == 1

    with open_session() as session, session.begin():
        assert build_repositories(session).jobs.prepare_retry(job_id) is not None
    parse_version_job(job_id)

    assert len([job for job in _jobs_of(job_id) if job.stage == JobStage.EMBED]) == 1


# ------------------------------------------------ 按阶段投递与 outbox 语义


def _create_job(client, stage: str) -> int:
    material_id, version_id = create_source(client, texts=("一段文字。",))
    with open_session() as session, session.begin():
        return (
            build_repositories(session)
            .jobs.create(workspace_id(client), material_id, version_id, stage)
            .id
        )


def test_dispatch_routes_by_stage(client):
    queue = RecordingQueue()
    parse_id = _create_job(client, JobStage.PARSE)
    embed_id = _create_job(client, JobStage.EMBED)

    # dispatch_job 自己提交（outbox 语义），调用方不另行开启事务
    with open_session() as session:
        repos = build_repositories(session)
        assert dispatch_job(repos, queue, parse_id) is True
        assert dispatch_job(repos, queue, embed_id) is True

    assert queue.parse_calls == [parse_id]
    assert queue.index_calls == [embed_id]
    assert get_job(parse_id).enqueued_at is not None
    assert get_job(embed_id).enqueued_at is not None


def test_unknown_stage_is_marked_failed_without_dispatch(client):
    queue = RecordingQueue()
    job_id = _create_job(client, JobStage.PARSE)
    with open_session() as session, session.begin():
        session.get(ProcessingJob, job_id).stage = "extract"
    with open_session() as session:
        assert dispatch_job(build_repositories(session), queue, job_id) is False

    job = get_job(job_id)
    assert job.status == JobStatus.FAILED and job.retryable is False
    assert queue.parse_calls == [] and queue.index_calls == []


def test_dispatch_failure_stays_pending_for_compensation(client):
    job_id = _create_job(client, JobStage.EMBED)
    with open_session() as session:
        assert dispatch_job(build_repositories(session), BrokenQueue(), job_id) is False

    job = get_job(job_id)
    assert job.status == JobStatus.PENDING and job.enqueued_at is None
    assert job.delivery_attempts == 1

    queue = RecordingQueue()
    with open_session() as session:
        outcome = dispatch_pending_jobs(build_repositories(session), queue)
    assert outcome.delivered == 1 and outcome.failed == 0
    assert queue.index_calls == [job_id]
    assert get_job(job_id).enqueued_at is not None


def test_dispatch_ignores_missing_job(client):
    queue = RecordingQueue()
    with open_session() as session:
        assert dispatch_job(build_repositories(session), queue, 999999) is False
    assert queue.parse_calls == [] and queue.index_calls == []


def test_queue_keys_are_stable_per_job():
    assert rq_index_job_key(7) == "index-7"
    assert rq_job_key(7) != rq_index_job_key(7)


def test_database_allows_only_one_embed_job_per_version(client):
    from sqlalchemy.exc import IntegrityError

    material_id, version_id = create_source(client, texts=("一段文字。",))
    with open_session() as session, session.begin():
        build_repositories(session).jobs.create(
            workspace_id(client), material_id, version_id, JobStage.EMBED
        )
    with pytest.raises(IntegrityError):
        with open_session() as session, session.begin():
            build_repositories(session).jobs.create(
                workspace_id(client), material_id, version_id, JobStage.EMBED
            )


def test_create_or_requeue_transitions(client):
    material_id, version_id = create_source(client, texts=("一段文字。",))
    workspace = workspace_id(client)
    with open_session() as session, session.begin():
        repos = build_repositories(session)
        job = repos.jobs.create(workspace, material_id, version_id, JobStage.EMBED)
        assert (
            repos.jobs.create_or_requeue(workspace, material_id, version_id, JobStage.EMBED).status
            == JobStatus.PENDING
        )

        # 处理中保持不动，避免两个执行者同时消费
        session.get(ProcessingJob, job.id).status = JobStatus.RUNNING
        session.flush()
        assert (
            repos.jobs.create_or_requeue(workspace, material_id, version_id, JobStage.EMBED).status
            == JobStatus.RUNNING
        )

        # 终态重置为待处理，并清空上次的错误与投递时间
        row = session.get(ProcessingJob, job.id)
        row.status = JobStatus.PARTIAL
        row.error_reason = "上次失败"
        row.enqueued_at = row.created_at
        session.flush()
        reset = repos.jobs.create_or_requeue(workspace, material_id, version_id, JobStage.EMBED)
        assert reset.status == JobStatus.PENDING
        assert reset.error_reason is None and reset.enqueued_at is None
