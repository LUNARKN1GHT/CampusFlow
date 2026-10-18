"""Q004 编排用例：领取、模型调用、写回、失败与中断恢复（真实 PostgreSQL）。"""

from index_helpers import (
    DEFAULT_TEXTS,
    FakeEmbedder,
    block_rows,
    create_embed_job,
    create_source,
    get_job,
    open_session,
    run_index,
    workspace_id,
)
from sqlalchemy import delete, select

from campusflow.domain.errors import NotFoundError, PermanentError, TransientError
from campusflow.domain.states import JobStatus, MaterialStatus
from campusflow.infrastructure.db.models import Material, MaterialVersion, SourceChunk
from campusflow.infrastructure.repositories import build_repositories


def test_full_run_embeds_every_block_and_marks_job_done(client):
    embedder = FakeEmbedder()
    job_id, _, _ = create_embed_job(client)

    summary = run_index(job_id, embedder)

    assert summary is not None and summary.fully_searchable
    assert summary.status == JobStatus.DONE
    assert len(embedder.calls) == 2
    job = get_job(job_id)
    assert job.status == JobStatus.DONE and job.retryable in (None, False)
    rows = block_rows(job_id)
    assert [row["state"] for row in rows] == ["done", "done"]
    assert all(row["has_embedding"] for row in rows)
    assert all(row["attempts"] == 1 for row in rows)


def test_duplicate_message_does_not_call_model_again(client):
    embedder = FakeEmbedder()
    job_id, _, _ = create_embed_job(client)
    run_index(job_id, embedder)
    calls = len(embedder.calls)

    assert run_index(job_id, embedder) is None
    assert len(embedder.calls) == calls
    assert get_job(job_id).status == JobStatus.DONE


def test_permanent_failure_keeps_successful_blocks_and_reports_partial(client):
    embedder = FakeEmbedder()
    embedder.fail_next(PermanentError("模型拒绝"))
    job_id, _, _ = create_embed_job(client)

    summary = run_index(job_id, embedder)

    assert summary.status == JobStatus.PARTIAL
    assert summary.indexed == summary.failed == 1
    assert not summary.fully_searchable
    job = get_job(job_id)
    assert job.status == JobStatus.PARTIAL and job.retryable is False
    rows = block_rows(job_id)
    assert rows[0]["state"] == "failed" and rows[0]["failure_code"] == "invalid_response"
    assert rows[1]["state"] == "done"


def test_transient_failure_is_retried_until_success(client):
    embedder = FakeEmbedder()
    embedder.fail_next(TransientError("上游 503"))
    embedder.fail_next(TransientError("上游超时"))
    job_id, _, _ = create_embed_job(client)

    summary = run_index(job_id, embedder)

    assert summary.status == JobStatus.DONE and summary.fully_searchable
    assert len(embedder.calls) == 4  # 同一块重试两次，另一块一次
    assert [row["attempts"] for row in block_rows(job_id)] == [3, 1]


def test_attempts_exhausted_stops_calling_model(client):
    embedder = FakeEmbedder()
    for _ in range(3):
        embedder.fail_next(TransientError("上游 503"))
    job_id, _, _ = create_embed_job(client)

    summary = run_index(job_id, embedder)

    assert summary.indexed == summary.failed == 1
    assert summary.retryable_failed == 0
    rows = block_rows(job_id)
    assert rows[0]["attempts"] == 3
    assert rows[0]["failure_code"] == "attempts_exhausted"
    assert rows[0]["retryable"] is False
    assert len(embedder.calls) == 4  # 3 次失败 + 另一个块 1 次，没有第 4 次重试


def test_unknown_coverage_never_claims_fully_searchable(client):
    embedder = FakeEmbedder()
    job_id, _, _ = create_embed_job(client, coverage="unknown")

    summary = run_index(job_id, embedder)

    assert summary.indexed == 2
    assert not summary.source_coverage_known
    assert not summary.fully_searchable
    assert summary.status == JobStatus.PARTIAL
    assert get_job(job_id).status == JobStatus.PARTIAL


def test_empty_version_does_not_pretend_to_be_searchable(client):
    embedder = FakeEmbedder()
    job_id, _, _ = create_embed_job(client, texts=())

    summary = run_index(job_id, embedder)

    assert summary.expected == 0
    assert not summary.fully_searchable
    assert get_job(job_id).status == JobStatus.FAILED
    assert embedder.calls == []


def test_material_deleted_mid_run_discards_result(client):
    embedder = FakeEmbedder()
    job_id, material_id, _ = create_embed_job(client)

    def remove_material(_text: str, _call: int) -> None:
        with open_session() as session, session.begin():
            session.execute(delete(Material).where(Material.id == material_id))

    embedder.on_embed = remove_material
    assert run_index(job_id, embedder) is None

    # 资料删除会级联删除作业；晚到的结果既不能复活资料，也不留下块
    assert get_job(job_id) is None
    assert block_rows(job_id) == []


def test_version_superseded_mid_run_is_not_retried(client):
    embedder = FakeEmbedder()
    job_id, material_id, _ = create_embed_job(client)

    def add_version(_text: str, _call: int) -> None:
        with open_session() as session, session.begin():
            session.add(MaterialVersion(material_id=material_id, version_no=2, note="v2"))

    embedder.on_embed = add_version
    assert run_index(job_id, embedder) is None

    job = get_job(job_id)
    assert job.status == JobStatus.FAILED
    assert job.retryable is False
    assert "更新的版本" in job.error_reason


def test_source_changed_mid_run_is_retryable_and_keeps_done_blocks(client):
    embedder = FakeEmbedder()
    job_id, _, version_id = create_embed_job(client)

    def change_second_source(_text: str, call: int) -> None:
        if call != 1:
            return
        with open_session() as session, session.begin():
            second = session.scalar(
                select(SourceChunk)
                .where(SourceChunk.version_id == version_id)
                .order_by(SourceChunk.seq.desc())
            )
            second.text = "第二段内容被重新解析覆盖。"

    embedder.on_embed = change_second_source
    assert run_index(job_id, embedder) is None

    job = get_job(job_id)
    assert job.status == JobStatus.FAILED and job.retryable is True
    rows = block_rows(job_id)
    assert rows[0]["state"] == "done"
    assert rows[1]["state"] == "pending"

    # 来源恢复后重跑：已完成的块不会被再次调用模型
    with open_session() as session, session.begin():
        second = session.scalar(
            select(SourceChunk)
            .where(SourceChunk.version_id == version_id)
            .order_by(SourceChunk.seq.desc())
        )
        second.text = DEFAULT_TEXTS[1]
    with open_session() as session, session.begin():
        assert build_repositories(session).jobs.prepare_retry(job_id) is not None
    embedder2 = FakeEmbedder()
    summary = run_index(job_id, embedder2)
    assert summary.status == JobStatus.DONE
    assert DEFAULT_TEXTS[0] not in embedder2.calls
    assert embedder2.calls == [DEFAULT_TEXTS[1]]


def test_stale_claim_is_taken_over_and_old_token_cannot_write(client):
    embedder = FakeEmbedder()
    job_id, material_id, version_id = create_embed_job(client)

    # 模拟崩溃的 Worker：领取一整块后进程消失，租约很快过期
    from campusflow.infrastructure.embedding_profiles import SqlEmbeddingProfiles
    from campusflow.infrastructure.index_manifests import SqlIndexManifests

    with open_session() as session, session.begin():
        profiles = SqlEmbeddingProfiles(session)
        profiles.register(embedder.profile)
        manifests = SqlIndexManifests(session)
        manifest_id = manifests.prepare(workspace_id(client), version_id, embedder.profile.id)
        stale = manifests.claim_next(workspace_id(client), manifest_id, lease_seconds=1)
        assert stale is not None

    import time

    time.sleep(1.2)

    summary = run_index(job_id, embedder)
    assert summary.status == JobStatus.DONE
    rows = block_rows(job_id)
    assert all(row["state"] == "done" for row in rows)
    assert rows[0]["attempts"] == 2  # 崩溃那次也算一次


def test_archived_source_is_not_retried(client):
    embedder = FakeEmbedder()
    material_id, version_id = create_source(client, archived=True)
    with open_session() as session, session.begin():
        job = build_repositories(session).jobs.create(
            workspace_id(client), material_id, version_id, "embed"
        )
        job_id = job.id

    assert run_index(job_id, embedder) is None
    job = get_job(job_id)
    assert job.status == JobStatus.FAILED and job.retryable is False
    assert embedder.calls == []


def test_material_status_is_never_touched_by_indexing(client):
    embedder = FakeEmbedder()
    job_id, material_id, _ = create_embed_job(client)
    with open_session() as session, session.begin():
        repos = build_repositories(session)
        repos.materials.set_status(material_id, MaterialStatus.DONE)

    run_index(job_id, embedder)

    with open_session() as session:
        assert build_repositories(session).materials.get(material_id).status == MaterialStatus.DONE


def test_missing_job_is_noop(client):
    assert run_index(999999, FakeEmbedder()) is None


def test_not_found_from_embedder_maps_to_source_unavailable(client):
    embedder = FakeEmbedder()
    embedder.fail_next(NotFoundError("来源块已删除"))
    job_id, _, _ = create_embed_job(client)

    summary = run_index(job_id, embedder)

    assert summary.failed == 1
    rows = block_rows(job_id)
    assert rows[0]["failure_code"] == "source_unavailable"
    assert rows[0]["retryable"] is False
    # 另一个块仍然成功，不被单块永久失败拖累
    assert summary.indexed == 1


def test_unexpected_error_is_bounded_by_attempt_limit(client):
    embedder = FakeEmbedder()

    def boom(_text: str, _call: int) -> None:
        raise RuntimeError("未预期故障")

    embedder.on_embed = boom
    job_id, _, _ = create_embed_job(client, texts=(DEFAULT_TEXTS[0],))

    summary = run_index(job_id, embedder)

    assert summary.failed == 1
    rows = block_rows(job_id)
    assert rows[0]["attempts"] == 3
    assert rows[0]["failure_code"] == "attempts_exhausted"
    assert len(embedder.calls) == 3
