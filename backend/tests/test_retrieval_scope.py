"""Q005：真实 PostgreSQL 范围隔离；全部原文为合成数据，不调用模型服务。"""

from dataclasses import replace
from datetime import date
from unittest.mock import Mock

import pytest
from index_helpers import (
    FakeEmbedder,
    create_embed_job,
    get_job,
    open_session,
    run_index,
    workspace_id,
)
from sqlalchemy import select

from campusflow.application.retrieval import collect_retrieval_candidates
from campusflow.domain.chunking import ChunkingConfig
from campusflow.domain.embeddings import EmbeddingProfile, cosine_similarity
from campusflow.domain.errors import DomainError, ModelOutputError, NotFoundError
from campusflow.domain.retrieval import RetrievalScope
from campusflow.infrastructure.db.models import (
    Course,
    IndexedBlock,
    Material,
    MaterialVersion,
    ProcessingJob,
    Semester,
    SourceChunk,
    Workspace,
)
from campusflow.infrastructure.embedding_profiles import SqlEmbeddingProfiles
from campusflow.infrastructure.index_manifests import SqlIndexManifests
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.retrieval import SqlRetrievalCandidates


def scopes(client):
    workspace = workspace_id(client)
    with open_session() as session, session.begin():
        other = Workspace(name="另一个人的空间")
        session.add(other)
        session.flush()
        semesters = [
            Semester(
                workspace_id=owner,
                name=name,
                start_date=date(2026, 9, 1),
                end_date=date(2027, 1, 31),
            )
            for owner, name in ((workspace, "秋季"), (workspace, "春季"), (other.id, "他人秋季"))
        ]
        session.add_all(semesters)
        session.flush()
        courses = [
            Course(
                workspace_id=semester.workspace_id,
                semester_id=semester.id,
                name="数据库原理",
                class_name=class_name,
            )
            for semester, class_name in (
                (semesters[0], "一班"),
                (semesters[0], "二班"),
                (semesters[2], "一班"),
            )
        ]
        session.add_all(courses)
        session.flush()
        return workspace, other.id, [s.id for s in semesters], [c.id for c in courses]


def indexed(
    client, *, scope=None, owner=None, texts=("合成作业十月二十日截止。",), coverage="complete"
):
    job, material, version = create_embed_job(client, texts=texts, coverage=coverage)
    model = FakeEmbedder()
    run_index(job, model)
    with open_session() as session, session.begin():
        row = session.get(Material, material)
        if scope:
            row.semester_id, row.course_id, row.class_name = scope
        if owner:
            row.workspace_id = owner
            session.get(ProcessingJob, job).workspace_id = owner
            # 同时改变整个索引归属，避免只凭任一冗余空间字段通过测试。
            from campusflow.infrastructure.db.models import IndexManifest

            session.get(IndexManifest, get_job(job).manifest_id).workspace_id = owner
            for block in session.scalars(
                select(IndexedBlock).where(IndexedBlock.version_id == version)
            ):
                block.workspace_id = owner
    return job, material, version


def collect(client, scope=None, profile=None):
    with open_session() as session:
        return collect_retrieval_candidates(
            build_repositories(session),
            SqlRetrievalCandidates(session),
            workspace_id(client),
            scope or RetrievalScope(),
            (profile or EmbeddingProfile()).id,
        )


def test_scope_filters_before_any_source_validation_or_ranking(client, monkeypatch):
    workspace, other, semesters, courses = scopes(client)
    selected = (semesters[0], courses[0], "一班")
    _, allowed, version = indexed(client, scope=selected, texts=("允许的合成资料。",))
    for scope, owner in (
        (selected, other),
        ((semesters[1], None, "一班"), workspace),
        ((semesters[0], courses[1], "二班"), workspace),
        ((None, courses[0], "一班"), workspace),
        ((semesters[0], None, "一班"), workspace),
        ((semesters[0], courses[0], None), workspace),
    ):
        indexed(
            client,
            scope=scope,
            owner=owner if owner != workspace else None,
            texts=("禁止混入的合成资料。",),
        )
    original = SqlIndexManifests.summary
    validated = []

    def spy(self, owner, manifest, **kwargs):
        # 在读取任何来源块之前，候选已受 SQL 范围过滤。
        validated.append(manifest)
        return original(self, owner, manifest, **kwargs)

    monkeypatch.setattr(SqlIndexManifests, "summary", spy)
    result = collect(client, RetrievalScope(*selected))
    assert {c.material_id for c in result.candidates} == {allowed}
    assert {c.block.version_id for c in result.candidates} == {version}
    assert len(validated) == 1
    assert result.versions[0].progress.fully_searchable
    # 后续排序/上下文只能消费这批已过滤的候选。
    model_context = [candidate.block.text for candidate in result.candidates]
    assert model_context == ["允许的合成资料。"]


@pytest.mark.parametrize("dimension", ["semester_id", "course_id", "class_name"])
def test_each_explicit_dimension_excludes_unknown_independently(client, dimension):
    _, _, semesters, courses = scopes(client)
    _, allowed, _ = indexed(client, scope=(semesters[0], courses[0], "一班"))
    indexed(client)
    value = {"semester_id": semesters[0], "course_id": courses[0], "class_name": "一班"}[dimension]
    result = collect(client, RetrievalScope(**{dimension: value}))
    assert {item.material_id for item in result.candidates} == {allowed}


def test_unrestricted_workspace_can_read_unclassified_but_not_other_workspace(client):
    _, other, _, _ = scopes(client)
    _, allowed, _ = indexed(client)
    indexed(client, owner=other)
    assert {item.material_id for item in collect(client).candidates} == {allowed}


@pytest.mark.parametrize(
    "kind", ["foreign_semester", "foreign_course", "missing_semester", "missing_course"]
)
def test_unauthorized_ids_fail_before_reader(client, kind):
    workspace, _, semesters, courses = scopes(client)
    scope = {
        "foreign_semester": RetrievalScope(semester_id=semesters[2]),
        "foreign_course": RetrievalScope(course_id=courses[2]),
        "missing_semester": RetrievalScope(semester_id=999999),
        "missing_course": RetrievalScope(course_id=999999),
    }[kind]
    reader = Mock()
    with open_session() as session, pytest.raises(NotFoundError):
        collect_retrieval_candidates(
            build_repositories(session), reader, workspace, scope, "unused"
        )
    reader.collect.assert_not_called()


@pytest.mark.parametrize("kind", ["semester", "class"])
def test_contradictory_scope_rejected_before_reader(client, kind):
    workspace, _, semesters, courses = scopes(client)
    scope = (
        RetrievalScope(semesters[1], courses[0])
        if kind == "semester"
        else RetrievalScope(course_id=courses[0], class_name="二班")
    )
    reader = Mock()
    with open_session() as session, pytest.raises(DomainError):
        collect_retrieval_candidates(
            build_repositories(session), reader, workspace, scope, "unused"
        )
    reader.collect.assert_not_called()


@pytest.mark.parametrize(
    "scope",
    [
        {"semester_id": True},
        {"course_id": 0},
        {"semester_id": -1},
        {"course_id": "1"},
        {"class_name": ""},
        {"class_name": " 一班"},
        {"class_name": "一" * 101},
    ],
    ids=["bool", "zero", "negative", "string-id", "empty-class", "space", "long"],
)
def test_invalid_scope(scope):
    with pytest.raises(DomainError):
        RetrievalScope(**scope)


def test_live_archive_restore_and_scope_edit(client):
    _, _, semesters, courses = scopes(client)
    _, material, _ = indexed(client, scope=(semesters[0], courses[0], "一班"))
    scope = RetrievalScope(semesters[0], courses[0], "一班")
    assert collect(client, scope).candidates
    with open_session() as session, session.begin():
        session.get(Material, material).archived = True
    assert not collect(client, scope).candidates
    with open_session() as session, session.begin():
        session.get(Material, material).archived = False
    assert collect(client, scope).candidates
    with open_session() as session, session.begin():
        session.get(Material, material).class_name = "二班"
    assert not collect(client, scope).candidates
    assert collect(client, RetrievalScope(class_name="二班")).candidates


def test_archived_semester_still_explicitly_queryable(client):
    _, _, semesters, courses = scopes(client)
    indexed(client, scope=(semesters[0], courses[0], "一班"))
    with open_session() as session, session.begin():
        session.get(Semester, semesters[0]).archived = True
    assert collect(client, RetrievalScope(semester_id=semesters[0])).candidates


@pytest.mark.parametrize("mutation", ["delete", "new_version", "reset_job"])
def test_unavailable_versions_do_not_fall_back_to_old_index(client, mutation):
    job, material, _ = indexed(client)
    with open_session() as session, session.begin():
        if mutation == "delete":
            session.delete(session.get(Material, material))
        elif mutation == "new_version":
            session.add(MaterialVersion(material_id=material, version_no=2))
        else:
            session.get(ProcessingJob, job).manifest_id = None
    result = collect(client)
    assert not result.candidates


@pytest.mark.parametrize(
    "mutation", ["text", "location", "coverage", "extra_chunk", "block_text", "zero_vector"]
)
def test_changed_sources_and_corrupt_blocks_are_unavailable(client, mutation):
    _, _, version = indexed(client)
    with open_session() as session, session.begin():
        chunk = session.scalar(select(SourceChunk).where(SourceChunk.version_id == version))
        block = session.scalar(select(IndexedBlock).where(IndexedBlock.version_id == version))
        if mutation == "text":
            chunk.text = "更改后的合成原文。"
        elif mutation == "location":
            chunk.locator_value = "99"
        elif mutation == "coverage":
            session.get(MaterialVersion, version).source_metadata = {"coverage": None}
        elif mutation == "extra_chunk":
            session.add(
                SourceChunk(
                    version_id=version,
                    seq=99,
                    text="新增段落。",
                    locator_type="paragraph",
                    locator_value="100",
                    paragraph=100,
                )
            )
        elif mutation == "block_text":
            block.text = "错误文本。"
        else:
            block.embedding = [0.0] * 1024
    result = collect(client)
    assert not result.candidates
    assert result.versions[0].unavailable_reason == "index_unavailable"
    assert result.versions[0].progress is None


@pytest.mark.parametrize("coverage", ["partial", "unknown"])
def test_partial_and_unknown_coverage_preserved(client, coverage):
    indexed(client, coverage=coverage)
    result = collect(client)
    assert result.candidates
    progress = result.versions[0].progress
    assert not progress.fully_searchable
    if coverage == "partial":
        assert progress.uncovered_source_count == 1
    else:
        assert not progress.source_coverage_known


def test_partial_embedding_keeps_success_and_reports_gap(client):
    job, _, _ = create_embed_job(client)
    model = FakeEmbedder()
    model.fail_next(ModelOutputError("synthetic invalid vector"))
    run_index(job, model)
    result = collect(client)
    assert len(result.candidates) == 1
    assert result.versions[0].progress.failed == 1
    assert not result.versions[0].progress.fully_searchable


def test_profile_revision_never_mixed_and_unregistered_rejected(client):
    indexed(client)
    other = replace(EmbeddingProfile(), revision="next-generation")
    with open_session() as session, session.begin():
        SqlEmbeddingProfiles(session).register(other)
    assert not collect(client, profile=other).candidates
    with pytest.raises(DomainError):
        collect(client, profile=replace(other, revision="not-registered"))


def test_only_current_manifest_blocks_and_stable_order(client):
    job, _, version = indexed(client, texts=("第一句话。第二句话。第三句话。",))
    model = FakeEmbedder()
    with open_session() as session, session.begin():
        store = SqlIndexManifests(session)
        manifest = store.prepare(
            workspace_id(client), version, model.profile.id, config=ChunkingConfig(max_bytes=18)
        )
        while claim := store.claim_next(workspace_id(client), manifest):
            store.save_success(workspace_id(client), manifest, claim, model.embed(claim.text))
        session.get(ProcessingJob, job).manifest_id = manifest
    result = collect(client)
    assert len(result.candidates) == 3
    assert [c.block.start for c in result.candidates] == [0, 5, 10]
    assert result == collect(client)
    for candidate in result.candidates:
        assert candidate.manifest_id == manifest
        assert (
            cosine_similarity(model.embed(candidate.block.text), candidate.embedding, model.profile)
            > 0.999
        )


def test_database_failure_is_not_reported_as_no_materials(client, monkeypatch):
    indexed(client)

    def broken(*args, **kwargs):
        raise RuntimeError("synthetic database fault")

    monkeypatch.setattr(SqlIndexManifests, "summary", broken)
    with pytest.raises(RuntimeError, match="synthetic database fault"):
        collect(client)
