"""Q006–Q008：检索排序、引用候选校验与来源失效。"""

import pytest
from index_helpers import FakeEmbedder, create_embed_job, open_session, run_index, workspace_id

from campusflow.application.retrieval import collect_retrieval_candidates
from campusflow.domain.chunking import RetrievalBlock, SourceLocation
from campusflow.domain.citations import (
    CitationStatus,
    build_citation_set,
    stable_citation_id,
    validate_citation_ids,
)
from campusflow.domain.embeddings import EmbeddedText, EmbeddingProfile
from campusflow.domain.errors import DomainError
from campusflow.domain.ranking import RankingMode, keyword_score, rank_candidates
from campusflow.domain.retrieval import RetrievalCandidate, RetrievalScope
from campusflow.infrastructure.citation_sources import SqlCitationSources
from campusflow.infrastructure.db.models import Material, MaterialVersion, SourceChunk
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.retrieval import SqlRetrievalCandidates


def _candidate(text: str, material_id: int, *, vector=None) -> RetrievalCandidate:
    profile = EmbeddingProfile()
    block = RetrievalBlock(
        id=f"block-{material_id}",
        source_chunk_id=material_id,
        version_id=material_id,
        source_fingerprint="f" * 64,
        location=SourceLocation("paragraph", "1", paragraph=1),
        start=0,
        end=len(text),
        text=text,
    )
    values = vector or tuple([1.0] + [0.0] * 1023)
    return RetrievalCandidate(
        material_id=material_id,
        title=f"资料 {material_id}",
        semester_id=None,
        course_id=None,
        class_name=None,
        manifest_id=f"manifest-{material_id}",
        block=block,
        embedding=EmbeddedText(profile.id, values, profile.model),
    )


def test_keyword_ranking_uses_course_number_even_without_semantic_vectors():
    candidates = (
        _candidate("数据结构（CS102）作业于10月25日截止。", 1, vector=(1.0,) + (0.0,) * 1023),
        _candidate("数据库原理（CS101）作业于10月20日截止。", 2, vector=(1.0,) + (0.0,) * 1023),
        _candidate("实验报告格式要求。", 3, vector=(0.0, 1.0) + (0.0,) * 1022),
    )
    query = "CS101 第三次作业什么时候截止"
    assert keyword_score(query, candidates[1].block.text) > keyword_score(
        query, candidates[0].block.text
    )
    ranked = rank_candidates(candidates, query, mode=RankingMode.KEYWORD, limit=3)
    assert ranked[0].candidate.block.id == "block-2"
    assert [item.candidate.block.id for item in ranked] == ["block-2", "block-1", "block-3"]


def test_vector_and_hybrid_ranking_are_reproducible_and_validate_inputs():
    profile = EmbeddingProfile()
    query_embedding = EmbeddedText(profile.id, (1.0,) + (0.0,) * 1023, profile.model)
    candidates = (
        _candidate("数据库原理（CS101）作业。", 1, vector=(1.0,) + (0.0,) * 1023),
        _candidate("无关资料。", 2, vector=(0.0, 1.0) + (0.0,) * 1022),
    )
    vector_first = rank_candidates(
        candidates,
        "CS101 作业",
        mode=RankingMode.VECTOR,
        query_embedding=query_embedding,
        profile=profile,
    )
    vector_second = rank_candidates(
        candidates,
        "CS101 作业",
        mode=RankingMode.VECTOR,
        query_embedding=query_embedding,
        profile=profile,
    )
    assert [item.candidate.block.id for item in vector_first] == ["block-1", "block-2"]
    assert [item.rank for item in vector_first] == [1, 2]
    assert [(i.candidate.block.id, i.score) for i in vector_first] == [
        (i.candidate.block.id, i.score) for i in vector_second
    ]
    hybrid = rank_candidates(
        candidates,
        "CS101 作业",
        mode=RankingMode.HYBRID,
        query_embedding=query_embedding,
        profile=profile,
    )
    assert hybrid[0].candidate.block.id == "block-1"
    with pytest.raises(DomainError):
        rank_candidates(candidates, "CS101", mode=RankingMode.HYBRID)
    with pytest.raises(DomainError):
        rank_candidates(candidates, " ", mode=RankingMode.KEYWORD)


def test_citations_are_stable_and_only_accept_candidate_members():
    candidates = [_candidate("数据库原理作业 10月20日截止。", 1)]
    citations = build_citation_set(candidates)
    assert len(citations) == 1
    citation = citations[0]
    assert citation.id == stable_citation_id(1, 1, "block-1")
    assert validate_citation_ids([citation.id], candidates) == (citation,)
    with pytest.raises(DomainError):
        validate_citation_ids(["cit_not_in_candidates"], candidates)
    with pytest.raises(DomainError):
        validate_citation_ids([citation.id, citation.id], candidates)
    # 合法 ID 只说明属于候选集；支持度由 support_warnings 等独立检查。
    from campusflow.domain.qa import support_warnings

    assert support_warnings("作业 12月31日截止。", citations)


def _scope_rows(client):
    from datetime import date

    from campusflow.infrastructure.db.models import Course, Semester

    workspace = workspace_id(client)
    with open_session() as session, session.begin():
        semesters = [
            Semester(
                workspace_id=workspace,
                name=name,
                start_date=date(2026, 9, 1),
                end_date=date(2027, 1, 31),
            )
            for name in ("秋季", "春季")
        ]
        session.add_all(semesters)
        session.flush()
        courses = [
            Course(workspace_id=workspace, semester_id=semesters[0].id, name="数据库原理"),
            Course(workspace_id=workspace, semester_id=semesters[1].id, name="数据库原理"),
        ]
        session.add_all(courses)
        session.flush()
        return (
            [semester.id for semester in semesters],
            [course.id for course in courses],
        )


def _collect(client, scope: RetrievalScope | None = None):
    with open_session() as session:
        return collect_retrieval_candidates(
            build_repositories(session),
            SqlRetrievalCandidates(session),
            workspace_id(client),
            scope or RetrievalScope(),
            EmbeddingProfile().id,
        )


def _index(client, texts, scope=None):
    job, material, version = create_embed_job(client, texts=texts)
    run_index(job, FakeEmbedder())
    if scope is not None:
        with open_session() as session, session.begin():
            row = session.get(Material, material)
            row.semester_id, row.course_id, row.class_name = scope
    return material, version


def test_deleted_and_archived_sources_disappear_from_retrieval(client):
    material, version = _index(client, ("数据库原理作业 10月20日截止。",))
    assert len(_collect(client).candidates) == 1

    with open_session() as session, session.begin():
        session.get(Material, material).archived = True
    assert _collect(client).candidates == ()

    with open_session() as session, session.begin():
        session.get(Material, material).archived = False
    assert len(_collect(client).candidates) == 1

    with open_session() as session, session.begin():
        row = session.get(Material, material)
        session.delete(row)
    assert _collect(client).candidates == ()


def test_scope_revision_takes_effect_on_next_read(client):
    semesters, courses = _scope_rows(client)
    material, _ = _index(
        client,
        ("数据库原理作业 10月20日截止。",),
        (semesters[0], courses[0], "一班"),
    )
    allowed = RetrievalScope(semesters[0], courses[0], "一班")
    assert len(_collect(client, allowed).candidates) == 1
    assert _collect(client, RetrievalScope(semesters[1], courses[1], "一班")).candidates == ()

    with open_session() as session, session.begin():
        row = session.get(Material, material)
        row.semester_id = semesters[1]
        row.course_id = courses[1]
    assert _collect(client, allowed).candidates == ()
    moved = RetrievalScope(semesters[1], courses[1], "一班")
    assert len(_collect(client, moved).candidates) == 1


def test_citation_revalidation_marks_deleted_archived_superseded_and_changed(client):
    material, version = _index(client, ("数据库原理作业 10月20日截止。",))
    citation = build_citation_set(_collect(client).candidates)[0]

    def state():
        with open_session() as session:
            return SqlCitationSources(session).revalidate(workspace_id(client), (citation,))[0]

    assert state().status is CitationStatus.VALID

    with open_session() as session, session.begin():
        session.get(Material, material).archived = True
    assert state().status is CitationStatus.SOURCE_ARCHIVED
    with open_session() as session, session.begin():
        session.get(Material, material).archived = False

    with open_session() as session, session.begin():
        session.add(MaterialVersion(material_id=material, version_no=2))
    assert state().status is CitationStatus.VERSION_SUPERSEDED

    with open_session() as session, session.begin():
        session.delete(session.get(Material, material))
    assert state().status is CitationStatus.SOURCE_DELETED


def test_citation_revalidation_rejects_rewritten_source_text(client):
    material, version = _index(client, ("数据库原理作业 10月20日截止。",))
    citation = build_citation_set(_collect(client).candidates)[0]
    with open_session() as session, session.begin():
        chunk = session.query(SourceChunk).filter_by(version_id=version).one()
        chunk.text = "数据库原理作业 10月31日截止。"
    with open_session() as session:
        state = SqlCitationSources(session).revalidate(workspace_id(client), (citation,))[0]
    assert state.status is CitationStatus.SOURCE_CHANGED
    assert "不一致" in state.message
