"""#554：真实数据库的文本→后台→待核对/问答闭环，所有模型为合成替身。"""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from index_helpers import FakeEmbedder, get_job, open_session, run_index
from sqlalchemy import func, select
from test_task_extraction import CONTENT, output

from campusflow.application.extraction_jobs import extract_version
from campusflow.application.materials import request_ai_processing
from campusflow.domain.errors import TransientError
from campusflow.infrastructure.db.models import ExtractedItem, Material, ProcessingJob
from campusflow.infrastructure.queue.db_queue import DBJobQueue
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage


@pytest.fixture
def imported(client, monkeypatch, tmp_path):
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    monkeypatch.setattr(client.app.state, "job_queue", DBJobQueue())
    response = client.post(
        "/api/v1/materials/text",
        json={
            "title": "合成自动整理",
            "content": CONTENT,
            "process_ai": True,
        },
    )
    assert response.status_code == 201
    result = response.json()
    result["workspace_id"] = client.get("/api/v1/settings").json()["workspace_id"]
    return result


def stage_id(client, imported, stage):
    jobs = client.get(f"/api/v1/jobs?material_id={imported['material']['id']}").json()
    return next(job["id"] for job in jobs if job["stage"] == stage)


class Generator:
    def __init__(self, session, source, *, fail_second=False, hook=None):
        self.session, self.source = session, source
        self.calls = 0
        self.fail_second, self.hook = fail_second, hook

    def generate(self, request):
        assert not self.session.in_transaction(), "不得持有事务等待外部模型"
        self.calls += 1
        if self.hook and self.calls == 1:
            self.hook()
        if self.fail_second and self.calls == 2:
            raise TransientError("synthetic timeout")
        return json.dumps(
            output(self.source) if self.calls == 1 else {"items": []}, ensure_ascii=False
        )


def test_text_becomes_pending_candidates_and_fully_searchable(client, imported):
    extract_id = stage_id(client, imported, "extract")
    embed_id = stage_id(client, imported, "embed")
    assert len(imported["job_ids"]) == 2
    with open_session() as session:
        generator = Generator(session, imported)
        extract_version(build_repositories(session), generator, extract_id)
        assert get_job(extract_id).status == "done"
        assert generator.calls == 3
        extract_version(build_repositories(session), generator, extract_id)
        assert generator.calls == 3
    candidates = client.get("/api/v1/candidates").json()
    assert len(candidates) == 1
    assert candidates[0]["review_status"] == "pending"
    assert client.get("/api/v1/tasks").json() == []
    assert run_index(embed_id, FakeEmbedder()).fully_searchable
    response = client.post(f"/api/v1/materials/{imported['material']['id']}/process")
    assert response.status_code == 202
    assert sorted(response.json()["job_ids"]) == sorted(imported["job_ids"])
    assert get_job(extract_id).status == get_job(embed_id).status == "done"


def test_failed_extraction_is_atomic_and_manual_retry_creates_no_duplicates(client, imported):
    job_id = stage_id(client, imported, "extract")
    with open_session() as session:
        extract_version(
            build_repositories(session), Generator(session, imported, fail_second=True), job_id
        )
    assert get_job(job_id).status == "failed"
    assert client.get("/api/v1/candidates").json() == []
    assert client.post(f"/api/v1/jobs/{job_id}/retry").status_code == 200
    with open_session() as session:
        extract_version(build_repositories(session), Generator(session, imported), job_id)
    assert len(client.get("/api/v1/candidates").json()) == 1
    assert client.get("/api/v1/tasks").json() == []


@pytest.mark.parametrize("change", ["scope", "archived", "deleted", "reclaimed"])
def test_late_extraction_cannot_write_after_source_or_claim_changes(client, imported, change):
    job_id = stage_id(client, imported, "extract")

    def mutate():
        with open_session() as other, other.begin():
            material = other.get(Material, imported["material"]["id"])
            if change == "scope":
                material.class_name = "另一个教学班"
            elif change == "archived":
                material.archived = True
            elif change == "deleted":
                other.delete(material)
            else:
                job = other.get(ProcessingJob, job_id)
                job.started_at += timedelta(seconds=1)

    with open_session() as session:
        extract_version(
            build_repositories(session), Generator(session, imported, hook=mutate), job_id
        )
        assert session.scalar(select(func.count()).select_from(ExtractedItem)) == 0
    if change == "reclaimed":
        assert get_job(job_id).status == "running"


def test_save_only_can_later_start_once_under_concurrent_requests(client, monkeypatch, tmp_path):
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    source = client.post(
        "/api/v1/materials/text", json={"title": "仅存原文", "content": CONTENT}
    ).json()
    material_id = source["material"]["id"]
    assert source["job_ids"] == []
    workspace = client.get("/api/v1/settings").json()["workspace_id"]

    def start(_):
        with open_session() as session:
            return request_ai_processing(build_repositories(session), workspace, material_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(start, range(2)))
    assert results[0] == results[1]
    assert len(client.get(f"/api/v1/jobs?material_id={material_id}").json()) == 2


def test_dispatch_failure_keeps_source_and_jobs(client, monkeypatch, tmp_path):
    class BrokenQueue:
        def enqueue_extract(self, job_id):
            raise OSError("synthetic queue unavailable")

        enqueue_index = enqueue_extract

    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    monkeypatch.setattr(client.app.state, "job_queue", BrokenQueue())
    response = client.post(
        "/api/v1/materials/text", json={"title": "队列故障", "content": CONTENT, "process_ai": True}
    )
    assert response.status_code == 201
    for job_id in response.json()["job_ids"]:
        job = get_job(job_id)
        assert job.status == "pending"
        assert job.enqueued_at is None


def test_desktop_worker_executes_text_pipeline_without_redis(client, imported, monkeypatch):
    from campusflow.workers import embeddings, jobs
    from campusflow.workers.desktop import run_desktop_worker

    class SyntheticGenerator:
        calls = 0

        def generate(self, request):
            self.calls += 1
            return json.dumps(
                output(imported) if self.calls == 1 else {"items": []}, ensure_ascii=False
            )

    generator = SyntheticGenerator()
    monkeypatch.setattr(jobs, "make_extraction_generator", lambda settings: generator)
    monkeypatch.setattr(embeddings, "_FACTORY", lambda settings: FakeEmbedder())
    assert (
        run_desktop_worker(
            client.app.state.settings, burst=True, poll_interval=0, recover_on_start=False
        )
        == 2
    )
    assert generator.calls == 3
    assert get_job(stage_id(client, imported, "extract")).status == "done"
    assert get_job(stage_id(client, imported, "embed")).status == "done"
    assert len(client.get("/api/v1/candidates").json()) == 1
    assert client.get("/api/v1/tasks").json() == []


def test_demo_generator_never_creates_real_model(monkeypatch):
    from campusflow.core.config import Settings
    from campusflow.workers import extraction

    def forbidden(settings):
        pytest.fail("演示测试不允许创建真实模型")

    monkeypatch.setattr(extraction, "_DEMO", True)
    monkeypatch.setattr(extraction, "build_text_generator", forbidden)
    settings = Settings(_env_file=None, database_url="postgresql://local/synthetic_test")
    generator = extraction.make_extraction_generator(settings)
    assert json.loads(generator.generate(None)) == {"items": []}
    with pytest.raises(ValueError, match="专用测试库"):
        extraction.make_extraction_generator(
            settings.model_copy(update={"database_url": "postgresql://local/real_data"})
        )
