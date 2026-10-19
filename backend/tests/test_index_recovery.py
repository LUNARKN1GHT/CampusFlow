"""Q004 失败与中断恢复：阶段感知回收、心跳、自动重投上限与后台扫描。"""

import asyncio
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from conftest import TEST_DATABASE_URL
from fastapi.testclient import TestClient
from index_helpers import create_embed_job, create_source, get_job, open_session, workspace_id

from campusflow import main as main_module
from campusflow.application.jobs import (
    reclaim_stale_jobs,
    retry_auto_jobs,
    retry_job,
    run_recovery_sweep,
)
from campusflow.core.config import Settings
from campusflow.domain.states import JobStage, JobStatus, MaterialStatus
from campusflow.infrastructure.db.models import Material, ProcessingJob
from campusflow.infrastructure.repositories import build_repositories
from campusflow.main import create_app


class RecordingQueue:
    def __init__(self) -> None:
        self.index_calls: list[int] = []

    def enqueue_parse(self, job_id: int) -> str:
        return f"parse-{job_id}"

    def enqueue_index(self, job_id: int) -> str:
        self.index_calls.append(job_id)
        return f"index-{job_id}"


def _mark_running(job_id: int, *, started_seconds: int, heartbeat_seconds: int | None) -> None:
    with open_session() as session, session.begin():
        row = session.get(ProcessingJob, job_id)
        row.status = JobStatus.RUNNING
        row.started_at = datetime.now(UTC) - timedelta(seconds=started_seconds)
        row.heartbeat_at = (
            None
            if heartbeat_seconds is None
            else datetime.now(UTC) - timedelta(seconds=heartbeat_seconds)
        )


def _mark_failed(job_id: int, *, auto_retry_count: int = 0) -> None:
    with open_session() as session, session.begin():
        row = session.get(ProcessingJob, job_id)
        row.status = JobStatus.FAILED
        row.retryable = True
        row.attempts = 1
        row.auto_retry_count = auto_retry_count


def _create_parse_job(client) -> int:
    material_id, version_id = create_source(client, texts=("一段文字。",))
    with open_session() as session, session.begin():
        return (
            build_repositories(session)
            .jobs.create(workspace_id(client), material_id, version_id, JobStage.PARSE)
            .id
        )


def test_embed_timeout_is_reclaimed_without_touching_material_status(client):
    job_id, material_id, _ = create_embed_job(client)
    with open_session() as session, session.begin():
        build_repositories(session).materials.set_status(material_id, MaterialStatus.DONE)
    _mark_running(job_id, started_seconds=900, heartbeat_seconds=900)

    with open_session() as session:
        reclaimed = reclaim_stale_jobs(
            build_repositories(session), timeout_seconds=900, embed_timeout_seconds=60
        )

    assert [job.id for job in reclaimed] == [job_id]
    job = get_job(job_id)
    assert job.status == JobStatus.FAILED and job.retryable is True
    assert "超时" in job.error_reason
    with open_session() as session:
        # 资料早已解析成功：索引超时不能让它显示为失败
        assert build_repositories(session).materials.get(material_id).status == MaterialStatus.DONE


def test_parse_timeout_still_marks_material_failed(client):
    job_id = _create_parse_job(client)
    material_id = get_job(job_id).material_id
    _mark_running(job_id, started_seconds=900, heartbeat_seconds=None)

    with open_session() as session:
        reclaimed = reclaim_stale_jobs(
            build_repositories(session), timeout_seconds=60, embed_timeout_seconds=60
        )

    assert [job.id for job in reclaimed] == [job_id]
    with open_session() as session:
        status = build_repositories(session).materials.get(material_id).status
    assert status == MaterialStatus.FAILED


def test_fresh_heartbeat_keeps_long_index_job_running(client):
    job_id, _, _ = create_embed_job(client)
    _mark_running(job_id, started_seconds=3600, heartbeat_seconds=5)

    with open_session() as session:
        assert (
            reclaim_stale_jobs(
                build_repositories(session), timeout_seconds=900, embed_timeout_seconds=60
            )
            == []
        )
    assert get_job(job_id).status == JobStatus.RUNNING


def test_auto_retry_requeues_and_dispatches_until_cap(client):
    job_id, _, _ = create_embed_job(client)
    _mark_failed(job_id)
    queue = RecordingQueue()

    with open_session() as session:
        assert retry_auto_jobs(build_repositories(session), queue, max_auto_retries=2) == 1
    job = get_job(job_id)
    assert job.status == JobStatus.PENDING
    assert (job.auto_retry_count, job.enqueued_at is not None) == (1, True)
    assert queue.index_calls == [job_id]

    # 第二次失败后额度已用尽：不再自动重投，只留人工入口
    _mark_failed(job_id, auto_retry_count=2)
    with open_session() as session:
        assert retry_auto_jobs(build_repositories(session), queue, max_auto_retries=2) == 0
    assert get_job(job_id).status == JobStatus.FAILED


def test_auto_retry_only_handles_index_jobs(client):
    job_id = _create_parse_job(client)
    with open_session() as session, session.begin():
        row = session.get(ProcessingJob, job_id)
        row.status, row.retryable = JobStatus.FAILED, True

    with open_session() as session:
        retried = retry_auto_jobs(build_repositories(session), RecordingQueue(), max_auto_retries=3)
    assert retried == 0
    assert get_job(job_id).status == JobStatus.FAILED


def _session_context():
    @contextmanager
    def opener():
        with open_session() as session:
            yield build_repositories(session)

    return opener()


def test_sweep_isolates_failed_steps_and_still_reclaims(client):
    job_id, _, _ = create_embed_job(client)
    _mark_running(job_id, started_seconds=900, heartbeat_seconds=900)
    calls = {"count": 0}

    def opener():
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("数据库暂时不可用")
        return _session_context()

    result = run_recovery_sweep(
        opener,
        RecordingQueue(),
        timeout_seconds=900,
        embed_timeout_seconds=60,
    )

    assert result.delivered == 0
    assert result.reclaimed == 1
    # 第三步自动重投又把回收后的作业排回队列：前两步失败没有阻断后续步骤
    assert result.auto_retried == 1
    job = get_job(job_id)
    assert job.status == JobStatus.PENDING
    assert job.auto_retry_count == 1


def test_sweep_delivers_pending_jobs(client):
    job_id = _create_parse_job(client)
    queue = RecordingQueue()

    def opener():
        return _session_context()

    result = run_recovery_sweep(opener, queue)

    assert result.delivered == 1
    assert get_job(job_id).enqueued_at is not None


def test_startup_runs_one_sweep_when_interval_is_zero(monkeypatch):
    calls: list[object] = []
    monkeypatch.setattr(main_module, "run_recovery", lambda app: calls.append(app))
    settings = Settings(_env_file=None, database_url=TEST_DATABASE_URL, recovery_interval_seconds=0)

    with TestClient(create_app(settings)) as client:
        client.post(
            "/api/v1/auth/login",
            json={"username": settings.local_username, "password": settings.local_password},
        )
        time.sleep(0.15)

    assert len(calls) == 1


def test_background_loop_runs_while_server_is_up(monkeypatch):
    calls: list[object] = []
    monkeypatch.setattr(main_module, "run_recovery", lambda app: calls.append(app))
    settings = Settings(_env_file=None, database_url=TEST_DATABASE_URL, recovery_interval_seconds=1)

    with TestClient(create_app(settings)) as client:
        client.post(
            "/api/v1/auth/login",
            json={"username": settings.local_username, "password": settings.local_password},
        )
        time.sleep(1.3)

    # 启动一次 + 至少一轮周期扫描
    assert len(calls) >= 2


def test_recovery_loop_survives_errors_and_cancels_cleanly(monkeypatch):
    calls: list[object] = []

    def boom(app):
        calls.append(app)
        raise RuntimeError("单轮失败")

    monkeypatch.setattr(main_module, "run_recovery", boom)

    async def run() -> None:
        task = asyncio.create_task(main_module._recovery_loop(object(), 0))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()

    asyncio.run(run())
    assert calls


def test_executor_retry_resets_embed_job_without_touching_material(client):
    job_id, material_id, _ = create_embed_job(client)
    with open_session() as session, session.begin():
        build_repositories(session).materials.set_status(material_id, MaterialStatus.DONE)
        row = session.get(ProcessingJob, job_id)
        row.status, row.retryable = JobStatus.FAILED, True

    with open_session() as session:
        job = retry_job(build_repositories(session), RecordingQueue(), job_id)
        assert job.status == JobStatus.PENDING
        assert job.auto_retry_count == 0

    with open_session() as session:
        material = session.get(Material, material_id)
        assert material.status == MaterialStatus.DONE
