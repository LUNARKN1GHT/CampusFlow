"""#556：真实 PostgreSQL 中修复配置失败，模型调用只用合成实现。"""

import pytest
from index_helpers import (
    FakeEmbedder,
    block_rows,
    create_embed_job,
    get_job,
    open_session,
    run_index,
)

from campusflow.application.jobs import retry_auto_jobs
from campusflow.core.config import Settings
from campusflow.domain.errors import PermanentError
from campusflow.domain.jobs import allows_model_repair
from campusflow.infrastructure.db.models import Material, MaterialVersion
from campusflow.infrastructure.queue.db_queue import DBJobQueue
from campusflow.infrastructure.repositories import build_repositories
from campusflow.workers.assembly import UnavailableEmbedder


def configure(client, monkeypatch, key=None):
    monkeypatch.setattr(client.app.state.settings, "zhipu_api_key", key)
    monkeypatch.setattr(client.app.state, "job_queue", DBJobQueue())


def test_missing_configuration_waits_for_manual_repair(client, monkeypatch):
    configure(client, monkeypatch)
    job_id, _, _ = create_embed_job(client)
    run_index(job_id, UnavailableEmbedder(Settings(_env_file=None, ZHIPU_API_KEY=None)))
    assert get_job(job_id).status == "failed"
    assert get_job(job_id).retryable is False
    assert block_rows(job_id) == []
    with open_session() as session:
        assert retry_auto_jobs(build_repositories(session), DBJobQueue(), max_auto_retries=3) == 0
    assert client.get(f"/api/v1/jobs/{job_id}").json()["recovery_action"] == "configure_ai"
    assert client.post(f"/api/v1/jobs/{job_id}/retry").status_code == 400
    configure(client, monkeypatch, "synthetic-key")
    assert client.post(f"/api/v1/jobs/{job_id}/retry").status_code == 200
    assert client.post(f"/api/v1/jobs/{job_id}/retry").status_code == 400
    embedder = FakeEmbedder()
    assert run_index(job_id, embedder).fully_searchable
    calls = len(embedder.calls)
    run_index(job_id, embedder)
    assert len(embedder.calls) == calls
    assert client.get(f"/api/v1/jobs/{job_id}").json()["recovery_action"] is None


def test_legacy_failed_blocks_repaired_without_recomputing_successes(client, monkeypatch):
    configure(client, monkeypatch, "synthetic-key")
    job_id, _, _ = create_embed_job(client)
    first = FakeEmbedder()
    first.fail_next(PermanentError("synthetic rejected model"))
    summary = run_index(job_id, first)
    assert summary.indexed == summary.failed == 1
    before = block_rows(job_id)
    assert client.post(f"/api/v1/jobs/{job_id}/retry").status_code == 200
    second = FakeEmbedder()
    assert run_index(job_id, second).fully_searchable
    assert len(second.calls) == 1
    after = block_rows(job_id)
    successful = next(row for row in before if row["state"] == "done")
    assert next(row for row in after if row["block_id"] == successful["block_id"]) == successful


def test_missing_image_key_can_be_repaired_without_duplicate_chunks(client, monkeypatch, tmp_path):
    from test_jobs_api import _upload

    from campusflow.application import processing
    from campusflow.infrastructure.storage.local import LocalFileStorage
    from campusflow.workers.assembly import build_parser_registry

    configure(client, monkeypatch)
    storage = LocalFileStorage(tmp_path / "files")
    monkeypatch.setattr(client.app.state, "file_storage", storage)
    settings = Settings(
        _env_file=None, deployment_mode="desktop", data_dir=str(tmp_path), ZHIPU_API_KEY=None
    )
    monkeypatch.setattr(processing, "_PARSER_REGISTRY", build_parser_registry(settings))
    uploaded = _upload(client)
    job_id = uploaded["job_id"]
    with open_session() as session:
        with pytest.raises(PermanentError, match="未配置"):
            processing.parse_version(build_repositories(session), storage, job_id)
    assert client.get(f"/api/v1/jobs/{job_id}").json()["recovery_action"] == "configure_ai"
    configure(client, monkeypatch, "synthetic-key")
    assert client.post(f"/api/v1/jobs/{job_id}/retry").status_code == 200
    monkeypatch.setattr(
        processing, "_PARSER_REGISTRY", build_parser_registry(client.app.state.settings, demo=True)
    )
    with open_session() as session:
        repos = build_repositories(session)
        assert processing.parse_version(repos, storage, job_id)
        assert processing.parse_version(repos, storage, job_id) == []
    assert get_job(job_id).status == "done"


@pytest.mark.parametrize("blocked", ["archived", "superseded", "deleted"])
def test_model_repair_does_not_revive_unavailable_sources(client, monkeypatch, blocked):
    configure(client, monkeypatch, "synthetic-key")
    job_id, material_id, _ = create_embed_job(client)
    run_index(job_id, UnavailableEmbedder(Settings(_env_file=None, ZHIPU_API_KEY=None)))
    with open_session() as session, session.begin():
        material = session.get(Material, material_id)
        if blocked == "archived":
            material.archived = True
        elif blocked == "superseded":
            session.add(MaterialVersion(material_id=material_id, version_no=2))
        else:
            session.delete(material)
    assert client.post(f"/api/v1/jobs/{job_id}/retry").status_code in (400, 404)


@pytest.mark.parametrize(
    "reason,allowed",
    [
        ("未配置视觉模型密钥，无法识别图片；请在设置中填写模型密钥后重试", True),
        ("视觉识别请求被拒绝（HTTP 401），请检查模型及账号配置", True),
        ("图片格式不支持", False),
        ("来源已不可用", False),
        ("资料已有新版本", False),
    ],
)
def test_image_configuration_distinct_from_damaged_sources(reason, allowed):
    assert allows_model_repair("parse", "failed", reason) is allowed
    assert not allows_model_repair("parse", "done", reason)
