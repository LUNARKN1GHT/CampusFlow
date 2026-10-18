"""完整清单、真实 PostgreSQL 领取竞争与过期写回；全程使用预设向量。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier

import pytest
from conftest import TEST_DATABASE_URL
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from campusflow.domain.chunking import ChunkingConfig
from campusflow.domain.embeddings import EmbeddedText, EmbeddingProfile
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.indexing import validated_index_coverage
from campusflow.infrastructure.db.models import (
    IndexedBlock,
    IndexManifest,
    Material,
    MaterialVersion,
    SourceChunk,
)
from campusflow.infrastructure.embedding_profiles import SqlEmbeddingProfiles
from campusflow.infrastructure.index_manifests import SqlIndexManifests
from campusflow.infrastructure.indexed_blocks import SqlIndexedBlocks


@pytest.fixture
def sample(client):
    workspace = client.get("/api/v1/settings").json()["workspace_id"]
    engine = create_engine(TEST_DATABASE_URL)
    with Session(engine) as session, session.begin():
        material = Material(workspace_id=workspace, title="合成清单资料", source_type="text")
        session.add(material)
        session.flush()
        version = MaterialVersion(
            material_id=material.id,
            version_no=1,
            note="v1",
            source_metadata={"coverage": {"fragment_count": 2, "failures": []}},
        )
        session.add(version)
        session.flush()
        for seq, text in enumerate(["第一段合成内容。", "第二段合成内容。"]):
            session.add(
                SourceChunk(
                    version_id=version.id,
                    seq=seq,
                    text=text,
                    locator_type="paragraph",
                    locator_value=str(seq + 1),
                    paragraph=seq + 1,
                )
            )
        session.flush()
        profile = EmbeddingProfile()
        SqlEmbeddingProfiles(session).register(profile)
        manifest = SqlIndexManifests(session).prepare(workspace, version.id, profile.id)
        version_id = version.id
    yield engine, workspace, profile, version_id, manifest
    engine.dispose()


def claim_one(sample):
    engine, workspace, _, _, manifest = sample
    with Session(engine) as session, session.begin():
        return SqlIndexManifests(session).claim_next(workspace, manifest)


def finish(sample, claim, code=None):
    engine, workspace, profile, _, manifest = sample
    with Session(engine) as session, session.begin():
        store = SqlIndexManifests(session)
        if code:
            return store.save_failure(workspace, manifest, claim, code)
        return store.save_success(
            workspace, manifest, claim, EmbeddedText(profile.id, (0.1,) * 1024, profile.model)
        )


def expire(sample, claim):
    engine = sample[0]
    with Session(engine) as session, session.begin():
        row = session.get(IndexedBlock, (claim.profile_id, claim.block_id))
        row.lease_until = session.scalar(select(func.clock_timestamp())) - timedelta(seconds=1)


def test_complete_manifest_idempotence_and_missing_row_stays_in_denominator(sample):
    engine, workspace, profile, version_id, manifest = sample
    with Session(engine) as session, session.begin():
        store = SqlIndexManifests(session)
        assert store.prepare(workspace, version_id, profile.id) == manifest
        assert session.scalar(select(func.count()).select_from(IndexManifest)) == 1
        ids = session.get(IndexManifest, manifest).snapshot["block_ids"]
        assert len(ids) == 2
        session.execute(delete(IndexedBlock).where(IndexedBlock.block_id == ids[1]))
        summary = store.summary(workspace, manifest)
        assert summary.expected == summary.pending == 2
        assert not summary.fully_searchable
        # 幂等准备补缺失记录，而不是缩小分母。
        assert store.prepare(workspace, version_id, profile.id) == manifest
        assert session.scalar(select(func.count()).select_from(IndexedBlock)) == 2


def test_retry_only_failed_blocks_and_complete_only_after_all_success(sample):
    engine, workspace, _, _, manifest = sample
    first = claim_one(sample)
    assert finish(sample, first)
    second = claim_one(sample)
    assert second.block_id != first.block_id
    assert finish(sample, second, "upstream_transient")
    with Session(engine) as session:
        summary = SqlIndexManifests(session).summary(workspace, manifest)
        assert summary.indexed == summary.failed == 1
        assert summary.status == "partial" and not summary.fully_searchable
    retry = claim_one(sample)
    assert retry.block_id == second.block_id and retry.token != second.token
    assert not finish(sample, second)
    assert finish(sample, retry)
    assert claim_one(sample) is None
    with Session(engine) as session:
        summary = SqlIndexManifests(session).summary(workspace, manifest)
        assert summary.fully_searchable and summary.status == "done"


def test_concurrent_workers_claim_distinct_blocks_then_duplicate_message_gets_none(sample):
    barrier = Barrier(2)

    def claim(_):
        barrier.wait(timeout=5)
        return claim_one(sample)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, range(2)))
    assert len({claim.block_id for claim in claims}) == 2
    assert len({claim.token for claim in claims}) == 2
    assert claim_one(sample) is None


def test_expired_claim_is_rejected_before_and_after_reclaim(sample):
    old = claim_one(sample)
    expire(sample, old)
    assert not finish(sample, old)
    assert not finish(sample, old, "invalid_response")
    new = claim_one(sample)
    assert new.block_id == old.block_id and new.token != old.token
    assert not finish(sample, old)
    assert not finish(sample, old, "upstream_transient")
    assert finish(sample, new)
    assert not finish(sample, old)


def test_two_simultaneous_claims_for_one_block_only_issue_one_token(sample):
    engine, workspace, profile, _, manifest = sample
    with Session(engine) as session:
        block_id = session.get(IndexManifest, manifest).snapshot["block_ids"][0]
    barrier = Barrier(2)

    def claim(_):
        with Session(engine) as session, session.begin():
            barrier.wait(timeout=5)
            return SqlIndexedBlocks(session).claim(workspace, profile.id, block_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, range(2)))
    assert sum(claim is not None for claim in claims) == 1


def test_corrupted_manifest_cannot_report_success(sample):
    engine, workspace, _, _, manifest = sample
    with Session(engine) as session, session.begin():
        row = session.get(IndexManifest, manifest)
        row.snapshot = {**row.snapshot, "block_ids": []}
    with Session(engine) as session:
        with pytest.raises(DomainError, match="清单快照"):
            SqlIndexManifests(session).summary(workspace, manifest)


def test_forged_token_and_unclaimed_write_cannot_change_state(sample):
    engine, workspace, profile, _, manifest = sample
    with Session(engine) as session, session.begin():
        block_id = session.get(IndexManifest, manifest).snapshot["block_ids"][0]
        assert not SqlIndexedBlocks(session).save_success(
            workspace,
            profile.id,
            block_id,
            EmbeddedText(profile.id, (0.1,) * 1024, profile.model),
            token="forged",
        )
    claim = claim_one(sample)
    assert not finish(sample, replace(claim, token="forged"))
    assert finish(sample, claim)


def test_claim_transaction_rollback_releases_work(sample):
    engine, workspace, _, _, manifest = sample
    with Session(engine) as session:
        abandoned = SqlIndexManifests(session).claim_next(workspace, manifest)
        session.rollback()
    fresh = claim_one(sample)
    assert abandoned.block_id == fresh.block_id and abandoned.token != fresh.token
    assert not finish(sample, abandoned)
    assert finish(sample, fresh)


def test_manifest_creation_rollback_leaves_no_half_registered_generation(sample):
    engine, workspace, profile, version_id, manifest = sample
    with Session(engine) as session:
        new = SqlIndexManifests(session).prepare(
            workspace, version_id, profile.id, config=ChunkingConfig(max_bytes=12)
        )
        assert new != manifest
        session.rollback()
    with Session(engine) as session:
        assert session.get(IndexManifest, new) is None
        assert session.scalar(select(func.count()).select_from(IndexedBlock)) == 2


@pytest.mark.parametrize(
    "change", ["text", "coverage", "add_source", "delete_source", "archive", "version"]
)
def test_changed_sources_or_coverage_block_old_manifest_and_late_result(sample, change):
    engine, workspace, _, version_id, manifest = sample
    claim = claim_one(sample)
    with Session(engine) as session, session.begin():
        version = session.get(MaterialVersion, version_id)
        source = session.scalar(select(SourceChunk).order_by(SourceChunk.seq))
        if change == "text":
            source.text = "原文已更新"
        elif change == "coverage":
            version.source_metadata = {"coverage": {"fragment_count": 9, "failures": []}}
        elif change == "add_source":
            session.add(
                SourceChunk(
                    version_id=version_id,
                    seq=3,
                    text="新增段落",
                    locator_type="paragraph",
                    locator_value="4",
                )
            )
        elif change == "delete_source":
            session.delete(source)
        elif change == "archive":
            session.get(Material, version.material_id).archived = True
        else:
            session.add(MaterialVersion(material_id=version.material_id, version_no=2, note="v2"))
    for action in (lambda: finish(sample, claim), lambda: claim_one(sample)):
        with pytest.raises((DomainError, NotFoundError)):
            action()
    with Session(engine) as session:
        with pytest.raises((DomainError, NotFoundError)):
            SqlIndexManifests(session).summary(workspace, manifest)


def test_delete_material_removes_manifest_and_late_write_cannot_revive_it(sample):
    engine, workspace, _, version_id, manifest = sample
    claim = claim_one(sample)
    with Session(engine) as session, session.begin():
        material_id = session.get(MaterialVersion, version_id).material_id
        session.execute(delete(Material).where(Material.id == material_id))
    with pytest.raises(NotFoundError):
        finish(sample, claim)
    with Session(engine) as session:
        assert session.get(IndexManifest, manifest) is None
        assert session.scalar(select(func.count()).select_from(IndexedBlock)) == 0


@pytest.mark.parametrize(
    "coverage",
    [
        None,
        {},
        {"fragment_count": True, "failures": []},
        {"fragment_count": 1, "failures": []},
        {"fragment_count": 2, "failures": [None]},
        {
            "fragment_count": 2,
            "failures": [
                {
                    "locator_type": "page",
                    "locator_value": "3",
                    "reason": "无法识别",
                    "retryable": False,
                }
            ],
        },
    ],
    ids=["missing", "empty", "bool-count", "wrong-count", "invalid-failure", "partial-source"],
)
def test_incomplete_coverage_never_becomes_fully_searchable(sample, coverage):
    engine, workspace, profile, version_id, _ = sample
    with Session(engine) as session, session.begin():
        session.get(MaterialVersion, version_id).source_metadata = {"coverage": coverage}
        session.flush()
        manifest = SqlIndexManifests(session).prepare(workspace, version_id, profile.id)
    updated = (engine, workspace, profile, version_id, manifest)
    for _ in range(2):
        assert finish(updated, claim_one(updated))
    with Session(engine) as session:
        summary = SqlIndexManifests(session).summary(workspace, manifest)
        assert summary.indexed == 2 and not summary.fully_searchable
        assert summary.status == "partial"


@pytest.mark.parametrize("text", ["", "   "], ids=["empty", "blank"])
def test_empty_or_blank_source_does_not_disappear_from_coverage(sample, text):
    engine, workspace, profile, version_id, _ = sample
    with Session(engine) as session, session.begin():
        session.scalar(select(SourceChunk).order_by(SourceChunk.seq)).text = text
        session.flush()
        manifest = SqlIndexManifests(session).prepare(workspace, version_id, profile.id)
    updated = (engine, workspace, profile, version_id, manifest)
    assert finish(updated, claim_one(updated))
    assert claim_one(updated) is None
    with Session(engine) as session:
        summary = SqlIndexManifests(session).summary(workspace, manifest)
        assert not summary.fully_searchable and summary.status == "partial"


def test_profile_config_and_scope_are_isolated(sample):
    engine, workspace, profile, version_id, manifest = sample
    claim = claim_one(sample)
    assert finish(sample, claim)
    with Session(engine) as session, session.begin():
        store = SqlIndexManifests(session)
        other = replace(profile, revision="next")
        SqlEmbeddingProfiles(session).register(other)
        fresh = store.prepare(workspace, version_id, other.id)
        smaller = store.prepare(
            workspace, version_id, profile.id, config=ChunkingConfig(max_bytes=12)
        )
        assert len({manifest, fresh, smaller}) == 3
        assert store.summary(workspace, fresh).indexed == 0
        assert store.summary(workspace, smaller).indexed == 0
        with pytest.raises(DomainError):
            store.save_failure(workspace, fresh, claim, "upstream_transient")
        with pytest.raises(NotFoundError):
            store.claim_next(workspace + 100, manifest)


def test_permanent_failure_is_not_automatically_retried(sample):
    first = claim_one(sample)
    assert finish(sample, first, "invalid_response")
    second = claim_one(sample)
    assert first.block_id != second.block_id
    assert finish(sample, second)
    assert claim_one(sample) is None


def test_coverage_validation_retains_unknown_and_known_partial_distinction():
    assert validated_index_coverage({"fragment_count": 2, "failures": []}, 2) == (True, 0)
    assert validated_index_coverage({"fragment_count": 2, "failures": [None]}, 2) == (False, 1)
