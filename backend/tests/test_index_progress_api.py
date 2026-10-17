"""Q004 逐块进度接口：等待/处理中/部分成功/失败，且不泄露正文与向量。"""

from index_helpers import (
    FakeEmbedder,
    create_embed_job,
    create_source,
    open_session,
    run_index,
    workspace_id,
)

from campusflow.domain.errors import PermanentError
from campusflow.infrastructure.db.models import Material, MaterialVersion, Workspace
from campusflow.infrastructure.repositories import build_repositories


def _progress(client, job_id: int, **params):
    return client.get(f"/api/v1/jobs/{job_id}/index-progress", params=params)


def _create_parse_job(client) -> int:
    material_id, version_id = create_source(client, texts=("一段文字。",))
    with open_session() as session, session.begin():
        return (
            build_repositories(session)
            .jobs.create(workspace_id(client), material_id, version_id, "parse")
            .id
        )


def test_job_without_manifest_reports_waiting(client):
    job_id, _, _ = create_embed_job(client)

    response = _progress(client, job_id)

    assert response.status_code == 200
    assert response.json() == {
        "job_id": job_id,
        "manifest_id": None,
        "summary": None,
        "blocks": None,
    }
    assert _progress(client, job_id, include_blocks=True).json()["blocks"] == []


def test_progress_reports_full_success_without_block_details(client):
    embedder = FakeEmbedder()
    job_id, _, _ = create_embed_job(client)
    run_index(job_id, embedder)

    body = _progress(client, job_id).json()

    assert body["manifest_id"]
    assert body["blocks"] is None
    summary = body["summary"]
    assert summary["status"] == "done"
    assert summary["expected"] == summary["indexed"] == 2
    assert summary["failed"] == summary["pending"] == summary["running"] == 0
    assert summary["fully_searchable"] is True


def test_progress_lists_only_safe_block_fields(client):
    job_id, _, _ = create_embed_job(client)
    run_index(job_id, FakeEmbedder())

    body = _progress(client, job_id, include_blocks=True).json()
    assert [block["state"] for block in body["blocks"]] == ["done", "done"]
    for block in body["blocks"]:
        assert set(block) == {"block_id", "state", "attempts", "retryable", "failure_code"}
        assert block["attempts"] == 1


def test_partial_and_failed_blocks_are_visible(client):
    embedder = FakeEmbedder()
    embedder.fail_next(PermanentError("模型拒绝"))
    job_id, _, _ = create_embed_job(client)
    run_index(job_id, embedder)

    summary = _progress(client, job_id).json()["summary"]
    assert summary["status"] == "partial"
    assert summary["indexed"] == summary["failed"] == 1
    assert summary["fully_searchable"] is False

    failed = [block for block in _progress(client, job_id, include_blocks=True).json()["blocks"]]
    failure = next(block for block in failed if block["state"] == "failed")
    assert failure["failure_code"] == "invalid_response"
    assert failure["retryable"] is False


def test_non_index_job_is_rejected(client):
    job_id = _create_parse_job(client)
    response = _progress(client, job_id)
    assert response.status_code == 400
    assert "索引作业" in response.json()["detail"]


def test_unknown_job_is_not_found(client):
    assert _progress(client, 999999).status_code == 404


def test_other_workspace_job_is_not_found(client):
    with open_session() as session, session.begin():
        other = Workspace(name="另一个空间")
        session.add(other)
        session.flush()
        material = Material(workspace_id=other.id, title="别人的资料", source_type="text")
        session.add(material)
        session.flush()
        version = MaterialVersion(material_id=material.id, version_no=1)
        session.add(version)
        session.flush()
        job = build_repositories(session).jobs.create(other.id, material.id, version.id, "embed")
        foreign_id = job.id

    assert _progress(client, foreign_id).status_code == 404
