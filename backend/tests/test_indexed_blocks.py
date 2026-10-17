"""Q004 存储检查点：真实 pgvector、事务和版本隔离；没有模型请求。"""

from dataclasses import replace

import pytest
from conftest import TEST_DATABASE_URL
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from campusflow.domain.chunking import ChunkSource, SourceLocation, split_source
from campusflow.domain.embeddings import EmbeddedText, EmbeddingProfile
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.indexing import BlockIndexState
from campusflow.infrastructure.db.models import IndexedBlock, Material, MaterialVersion, SourceChunk
from campusflow.infrastructure.embedding_profiles import SqlEmbeddingProfiles
from campusflow.infrastructure.indexed_blocks import SqlIndexedBlocks


class ClaimingStore(SqlIndexedBlocks):
    """既有存储场景也先领取；独立领取和过期竞争在新测试中验证。"""

    def save_success(self, workspace_id, profile_id, block_id, result):
        claim = self.claim(workspace_id, profile_id, block_id)
        return super().save_success(
            workspace_id,
            profile_id,
            block_id,
            result,
            token=claim.token if claim else "unclaimed",
        )

    def save_failure(self, workspace_id, profile_id, block_id, code):
        claim = self.claim(workspace_id, profile_id, block_id)
        return super().save_failure(
            workspace_id,
            profile_id,
            block_id,
            code,
            token=claim.token if claim else "unclaimed",
        )


@pytest.fixture
def sample(client):
    workspace_id = client.get("/api/v1/settings").json()["workspace_id"]
    engine = create_engine(TEST_DATABASE_URL)
    with Session(engine) as session, session.begin():
        material = Material(workspace_id=workspace_id, title="合成索引资料", source_type="text")
        session.add(material)
        session.flush()
        version = MaterialVersion(material_id=material.id, version_no=1, note="v1")
        session.add(version)
        session.flush()
        source = SourceChunk(
            version_id=version.id,
            seq=0,
            locator_type="paragraph",
            locator_value="1",
            paragraph=1,
            text="合成原文，截止时间待核对。",
        )
        session.add(source)
        session.flush()
        block = split_source(
            ChunkSource(
                source.id, version.id, 0, source.text, SourceLocation("paragraph", "1", paragraph=1)
            )
        )[0]
        profile = EmbeddingProfile()
        SqlEmbeddingProfiles(session).register(profile)
        ClaimingStore(session).register(workspace_id, profile.id, block)
    yield engine, workspace_id, profile, block
    engine.dispose()


def vector(profile, value=0.1):
    return EmbeddedText(profile.id, (value,) * 1024, profile.model)


def test_actual_vector_storage_and_retry_preserve_first_success(sample):
    engine, workspace, profile, block = sample
    with Session(engine) as session, session.begin():
        store = ClaimingStore(session)
        store.register(workspace, profile.id, block)
        assert store.save_failure(workspace, profile.id, block.id, "upstream_transient")
        assert store.progress(workspace, profile.id, block.version_id)[0].retryable
        assert store.save_success(workspace, profile.id, block.id, vector(profile))
    with Session(engine) as session, session.begin():
        store = ClaimingStore(session)
        assert not store.save_success(workspace, profile.id, block.id, vector(profile, 0.2))
        assert not store.save_failure(workspace, profile.id, block.id, "upstream_transient")
        assert session.scalar(select(func.count()).select_from(IndexedBlock)) == 1
        row = session.get(IndexedBlock, (profile.id, block.id))
        assert list(row.embedding) == pytest.approx([0.1] * 1024)
        assert row.state == "done" and row.failure_code is None and not row.retryable
        # 使用真实 pgvector 运算核对列类型，不只是 JSON 或 Python 列表存储。
        distance = session.scalar(select(IndexedBlock.embedding.cosine_distance([0.1] * 1024)))
        assert distance == pytest.approx(0, abs=1e-6)


def test_model_generation_isolation_at_same_dimensions(sample):
    engine, workspace, profile, block = sample
    other = replace(profile, revision="next")
    with Session(engine) as session, session.begin():
        SqlEmbeddingProfiles(session).register(other)
        store = ClaimingStore(session)
        store.register(workspace, other.id, block)
        store.save_success(workspace, profile.id, block.id, vector(profile))
        assert (
            store.progress(workspace, other.id, block.version_id)[0].state
            == BlockIndexState.PENDING
        )
        with pytest.raises(DomainError):
            store.save_success(workspace, other.id, block.id, vector(profile))
        assert session.scalar(select(func.count()).select_from(IndexedBlock)) == 2


def test_rollback_does_not_leave_success_without_vector_or_partial_commit(sample):
    engine, workspace, profile, block = sample
    with Session(engine) as session:
        ClaimingStore(session).save_success(workspace, profile.id, block.id, vector(profile))
        session.rollback()
    with Session(engine) as session:
        row = session.get(IndexedBlock, (profile.id, block.id))
        assert row.state == "pending" and row.embedding is None


@pytest.mark.parametrize("value", [1e308, 1e-300])
def test_float32_overflow_and_all_zero_underflow_are_rejected(sample, value):
    engine, workspace, profile, block = sample
    with Session(engine) as session:
        with pytest.raises(DomainError):
            ClaimingStore(session).save_success(
                workspace, profile.id, block.id, vector(profile, value)
            )
        session.rollback()
        assert session.get(IndexedBlock, (profile.id, block.id)).state == "pending"


def test_foreign_workspace_and_raw_error_are_rejected(sample):
    engine, workspace, profile, block = sample
    with Session(engine) as session:
        store = ClaimingStore(session)
        with pytest.raises(NotFoundError):
            store.register(workspace + 100, profile.id, block)
        with pytest.raises(NotFoundError):
            store.save_success(workspace + 100, profile.id, block.id, vector(profile))
        with pytest.raises(DomainError):
            store.save_failure(workspace, profile.id, block.id, "Bearer private-secret")
        assert store.progress(workspace + 100, profile.id, block.version_id) == ()


def test_source_deleted_cascades_vectors_and_late_save_cannot_recreate(sample):
    engine, workspace, profile, block = sample
    with Session(engine) as session, session.begin():
        ClaimingStore(session).save_success(workspace, profile.id, block.id, vector(profile))
    with Session(engine) as session, session.begin():
        session.delete(session.get(SourceChunk, block.source_chunk_id))
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(IndexedBlock)) == 0
        with pytest.raises(NotFoundError):
            ClaimingStore(session).save_success(workspace, profile.id, block.id, vector(profile))


def test_modified_source_cannot_accept_old_vector(sample):
    engine, workspace, profile, block = sample
    with Session(engine) as session, session.begin():
        session.get(SourceChunk, block.source_chunk_id).text = "改变后的原文"
    with Session(engine) as session:
        with pytest.raises(DomainError):
            ClaimingStore(session).save_success(workspace, profile.id, block.id, vector(profile))


def test_two_concurrent_writers_preserve_one_success(sample):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    engine, workspace, profile, block = sample
    barrier = Barrier(2)

    def save(value):
        with Session(engine) as session, session.begin():
            barrier.wait(timeout=5)
            return ClaimingStore(session).save_success(
                workspace, profile.id, block.id, vector(profile, value)
            ), value

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, [0.1, 0.2]))
    assert sorted(success for success, _ in results) == [False, True]
    winner = next(value for success, value in results if success)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(IndexedBlock)) == 1
        assert list(session.get(IndexedBlock, (profile.id, block.id)).embedding) == pytest.approx(
            [winner] * 1024
        )


def test_superseded_version_rejects_late_write(sample):
    engine, workspace, profile, block = sample
    with Session(engine) as session, session.begin():
        material_id = session.get(MaterialVersion, block.version_id).material_id
        session.add(MaterialVersion(material_id=material_id, version_no=2, note="更新版"))
    with Session(engine) as session:
        with pytest.raises(DomainError, match="新版本"):
            ClaimingStore(session).save_success(workspace, profile.id, block.id, vector(profile))
        assert session.get(IndexedBlock, (profile.id, block.id)).embedding is None


def test_blank_source_is_explicitly_skipped(sample):
    engine, workspace, profile, block = sample
    with Session(engine) as session, session.begin():
        chunk = SourceChunk(
            version_id=block.version_id,
            seq=1,
            locator_type="paragraph",
            locator_value="2",
            paragraph=2,
            text="   ",
        )
        session.add(chunk)
        session.flush()
        blank = split_source(
            ChunkSource(
                chunk.id,
                chunk.version_id,
                1,
                chunk.text,
                SourceLocation("paragraph", "2", paragraph=2),
            )
        )[0]
        store = ClaimingStore(session)
        store.register(workspace, profile.id, blank)
        assert session.get(IndexedBlock, (profile.id, blank.id)).state == "skipped"
        with pytest.raises(DomainError, match="空白"):
            store.save_success(workspace, profile.id, blank.id, vector(profile))
