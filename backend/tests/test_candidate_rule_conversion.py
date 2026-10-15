"""R006：已确认规则候选转换为正式课程规则；幂等、守卫与来源失效留痕。"""

from datetime import date

import pytest

from campusflow.application.candidates import (
    get_candidate,
    revise_candidate_field,
    save_candidates,
    set_candidate_review_status,
)
from campusflow.application.conversion import convert_candidate_to_rule
from campusflow.application.materials import delete_material, preview_delete_impact
from campusflow.domain.candidates import (
    CandidateDraft,
    CandidateField,
    CandidateKind,
    CandidateScope,
    FieldEvidence,
    FieldOrigin,
)
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.rule_conversion import build_rule_conversion
from campusflow.domain.states import ReviewStatus
from campusflow.infrastructure.db.models import Course, ExtractedItem, Semester, Workspace
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.storage.local import LocalFileStorage

CONTENT = (
    "实验课纪律：迟到三次取消考试资格。\n\n适用 2026 秋季学期，自发布之日起生效。\n\n保留原文定位。"
)
QUOTE_TITLE = "实验课纪律"
QUOTE_RULE = "迟到三次取消考试资格"
QUOTE_CONDITIONS = "迟到三次"
QUOTE_SCOPE = "2026 秋季学期"
QUOTE_EFFECTIVE = "自发布之日起生效"


def rule_draft(
    chunk_id=1,
    *,
    scope_chunk_id=None,
    effective_chunk_id=None,
    title=QUOTE_TITLE,
    rule_text=QUOTE_RULE,
    conditions=QUOTE_CONDITIONS,
    scope_text=QUOTE_SCOPE,
    effective=QUOTE_EFFECTIVE,
    candidate_scope=None,
):
    # 标题、正文与条件出自第一段；范围与生效声明出自第二段
    scope_chunk = scope_chunk_id if scope_chunk_id is not None else chunk_id
    effective_chunk = effective_chunk_id if effective_chunk_id is not None else chunk_id
    fields = []
    if title is not None:
        fields.append(
            CandidateField(
                "title", title, FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, QUOTE_TITLE),)
            )
        )
    if rule_text is not None:
        fields.append(
            CandidateField(
                "rule_text",
                rule_text,
                FieldOrigin.DOCUMENT,
                (FieldEvidence(chunk_id, QUOTE_RULE),),
            )
        )
    if conditions is not None:
        fields.append(
            CandidateField(
                "conditions_text",
                conditions,
                FieldOrigin.DOCUMENT,
                (FieldEvidence(chunk_id, QUOTE_CONDITIONS),),
            )
        )
    if scope_text is not None:
        fields.append(
            CandidateField(
                "scope_text",
                scope_text,
                FieldOrigin.DOCUMENT,
                (FieldEvidence(scope_chunk, QUOTE_SCOPE),),
            )
        )
    if effective is not None:
        fields.append(
            CandidateField(
                "effective_text",
                effective,
                FieldOrigin.DOCUMENT,
                (FieldEvidence(effective_chunk, QUOTE_EFFECTIVE),),
            )
        )
    if not fields:
        fields.append(
            CandidateField(
                "note", "占位", FieldOrigin.SUGGESTION, explanation="保证候选至少一个字段"
            )
        )
    scope = candidate_scope if candidate_scope is not None else CandidateScope()
    return CandidateDraft(CandidateKind.RULE, tuple(fields), scope=scope)


def _rule_from_chunks(chunks, **kwargs):
    """按片段归属构造规则草稿，范围与生效声明定位到第二段。"""
    second = chunks[1]["id"] if len(chunks) > 1 else chunks[0]["id"]
    return rule_draft(
        chunks[0]["id"],
        scope_chunk_id=second,
        effective_chunk_id=second,
        **kwargs,
    )


def task_draft(chunk_id=1):
    return CandidateDraft(
        CandidateKind.TASK,
        (
            CandidateField(
                "title", "实验报告", FieldOrigin.DOCUMENT, (FieldEvidence(chunk_id, QUOTE_TITLE),)
            ),
        ),
    )


@pytest.fixture
def source(client, tmp_path, monkeypatch):
    monkeypatch.setattr(client.app.state, "file_storage", LocalFileStorage(tmp_path))
    response = client.post("/api/v1/materials/text", json={"title": "课程通知", "content": CONTENT})
    assert response.status_code == 201
    result = response.json()
    result["material"]["workspace_id"] = client.get("/api/v1/settings").json()["workspace_id"]
    return client, result["material"], result["chunks"]


def save(source, draft=None, *, confirm=True, **draft_kwargs):
    client, material, chunks = source
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        saved = save_candidates(
            repos,
            material["workspace_id"],
            material["id"],
            1,
            [draft or _rule_from_chunks(chunks, **draft_kwargs)],
        )[0]
        if confirm:
            set_candidate_review_status(
                repos,
                material["workspace_id"],
                saved.id,
                ReviewStatus.CONFIRMED,
                operator="student",
            )
        return get_candidate(repos, material["workspace_id"], saved.id)


def convert(source, candidate_id):
    client, material, _ = source
    with client.app.state.session_factory() as session:
        return convert_candidate_to_rule(
            build_repositories(session), material["workspace_id"], candidate_id
        )


def create_course(source, *, semester_name="秋季", course_name="数据库"):
    """在当前空间直接落库一个学期与课程，返回 (semester, course) 的 id。"""
    client, material, _ = source
    with client.app.state.session_factory() as session:
        semester = Semester(
            workspace_id=material["workspace_id"],
            name=semester_name,
            start_date=date(2026, 9, 1),
            end_date=date(2027, 1, 1),
        )
        session.add(semester)
        session.flush()
        course = Course(
            workspace_id=material["workspace_id"],
            semester_id=semester.id,
            name=course_name,
        )
        session.add(course)
        session.commit()
        return semester.id, course.id


def test_build_rule_conversion_maps_fields_and_normalizes_blank_optional_text():
    conversion = build_rule_conversion(CandidateKind.RULE, rule_draft().fields)
    assert conversion.title == QUOTE_TITLE
    assert conversion.rule_text == QUOTE_RULE
    assert conversion.conditions_text == QUOTE_CONDITIONS
    assert conversion.scope_text == QUOTE_SCOPE
    assert conversion.effective_text == QUOTE_EFFECTIVE
    blank = rule_draft(conditions="   ", scope_text="", effective=None).fields
    conversion = build_rule_conversion(CandidateKind.RULE, blank)
    assert conversion.conditions_text is None
    assert conversion.scope_text is None
    assert conversion.effective_text is None


def test_build_rule_conversion_rejects_non_rule_and_incomplete_or_long_title():
    with pytest.raises(DomainError, match="只有规则候选"):
        build_rule_conversion(CandidateKind.TASK, task_draft().fields)
    with pytest.raises(DomainError, match="缺少标题"):
        build_rule_conversion(CandidateKind.RULE, rule_draft(title=None).fields)
    with pytest.raises(DomainError, match="缺少标题"):
        build_rule_conversion(CandidateKind.RULE, rule_draft(title="   ").fields)
    with pytest.raises(DomainError, match="缺少规则正文"):
        build_rule_conversion(CandidateKind.RULE, rule_draft(rule_text=None).fields)
    with pytest.raises(DomainError, match="超过 200"):
        build_rule_conversion(CandidateKind.RULE, rule_draft(title="规" * 201).fields)


def test_convert_creates_linked_rule_with_all_fields(source):
    client, material, _ = source
    semester_id, course_id = create_course(source)
    candidate = save(source, candidate_scope=CandidateScope(semester_id, course_id))
    rule = convert(source, candidate.id)
    assert rule.title == QUOTE_TITLE
    assert rule.rule_text == QUOTE_RULE
    assert rule.conditions_text == QUOTE_CONDITIONS
    assert rule.scope_text == QUOTE_SCOPE
    assert rule.effective_text == QUOTE_EFFECTIVE
    assert rule.course_id == course_id
    assert rule.semester_id == semester_id
    assert rule.workspace_id == material["workspace_id"]
    assert rule.source_material_id == material["id"]
    assert rule.source_candidate_id == candidate.id
    assert rule.source_state == "available"
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        reloaded = get_candidate(repos, material["workspace_id"], candidate.id)
        assert reloaded.review_status is ReviewStatus.CONFIRMED
        assert len(repos.rules.list(material["workspace_id"])) == 1


def test_repeated_conversion_returns_existing_rule(source):
    client, material, _ = source
    candidate = save(source)
    first = convert(source, candidate.id)
    second = convert(source, candidate.id)
    assert first.id == second.id
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        assert len(repos.rules.list(material["workspace_id"])) == 1


def test_only_confirmed_rule_candidates_convert(source):
    client, material, _ = source
    pending = save(source, confirm=False)
    with pytest.raises(DomainError, match="只有已确认"):
        convert(source, pending.id)
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        set_candidate_review_status(
            repos,
            material["workspace_id"],
            pending.id,
            ReviewStatus.IGNORED,
            operator="student",
        )
    with pytest.raises(DomainError, match="只有已确认"):
        convert(source, pending.id)
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        set_candidate_review_status(
            repos,
            material["workspace_id"],
            pending.id,
            ReviewStatus.PENDING,
            operator="student",
        )
        set_candidate_review_status(
            repos,
            material["workspace_id"],
            pending.id,
            ReviewStatus.SUPERSEDED,
            operator="student",
        )
    with pytest.raises(DomainError, match="只有已确认"):
        convert(source, pending.id)
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        assert repos.rules.list(material["workspace_id"]) == []


def test_non_rule_candidate_cannot_become_rule(source):
    saved = save(source, task_draft())
    with pytest.raises(DomainError, match="只有规则候选"):
        convert(source, saved.id)


def test_missing_title_or_rule_text_blocks_conversion(source):
    client, material, _ = source
    no_title = save(source, title=None)
    no_rule_text = save(source, rule_text=None)
    with pytest.raises(DomainError, match="缺少标题"):
        convert(source, no_title.id)
    with pytest.raises(DomainError, match="缺少规则正文"):
        convert(source, no_rule_text.id)
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        assert repos.rules.list(material["workspace_id"]) == []


def test_title_over_200_characters_blocks_conversion(source):
    client, material, _ = source
    saved = save(source, title="规" * 201)
    with pytest.raises(DomainError, match="超过 200"):
        convert(source, saved.id)
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        assert repos.rules.list(material["workspace_id"]) == []


def test_user_corrected_fields_take_effect_in_conversion(source):
    client, material, _ = source
    saved = save(source)
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        revise_candidate_field(
            repos,
            material["workspace_id"],
            saved.id,
            "title",
            value="实验室安全守则",
            origin=FieldOrigin.USER,
            operator="student",
        )
        revise_candidate_field(
            repos,
            material["workspace_id"],
            saved.id,
            "effective_text",
            value="2026-11-01 起生效",
            origin=FieldOrigin.USER,
            operator="student",
        )
    rule = convert(source, saved.id)
    assert rule.title == "实验室安全守则"
    assert rule.effective_text == "2026-11-01 起生效"
    # 其余未修正字段保持原文取值
    assert rule.rule_text == QUOTE_RULE
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        reloaded = get_candidate(repos, material["workspace_id"], saved.id)
        title = next(f for f in reloaded.draft.fields if f.name == "title")
        assert title.value == QUOTE_TITLE


def test_rules_list_filters_by_course_and_semester(source):
    client, material, chunks = source
    first_semester, first_course = create_course(source)
    second_semester, second_course = create_course(
        source, semester_name="春季", course_name="操作系统"
    )
    first = save(
        source,
        _rule_from_chunks(chunks, candidate_scope=CandidateScope(first_semester, first_course)),
    )
    second = save(
        source,
        _rule_from_chunks(chunks, candidate_scope=CandidateScope(second_semester, second_course)),
    )
    first_rule = convert(source, first.id)
    second_rule = convert(source, second.id)
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        assert len(repos.rules.list(material["workspace_id"])) == 2
        by_course = repos.rules.list(material["workspace_id"], course_id=first_course)
        assert [rule.id for rule in by_course] == [first_rule.id]
        by_semester = repos.rules.list(material["workspace_id"], semester_id=second_semester)
        assert [rule.id for rule in by_semester] == [second_rule.id]


def test_deleting_material_keeps_rule_and_marks_source_invalidated(source):
    client, material, _ = source
    candidate = save(source)
    rule = convert(source, candidate.id)
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        impact = preview_delete_impact(repos, material["workspace_id"], material["id"])
        assert rule.id in impact.linked_rule_ids
        delete_material(
            repos,
            client.app.state.file_storage,
            material["workspace_id"],
            material["id"],
            confirm_token=impact.confirm_token,
            keep_tasks=True,
        )
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        reloaded = repos.rules.get(rule.id)
        assert reloaded is not None
        assert reloaded.source_material_id is None
        assert reloaded.source_candidate_id is None
        assert reloaded.source_state == "invalidated"
        assert reloaded.source_invalidated_note
        assert material["title"] in reloaded.source_invalidated_note
        # 规则本身不随资料删除，仍在当前空间的规则列表中
        assert [item.id for item in repos.rules.list(material["workspace_id"])] == [rule.id]


def test_foreign_course_or_semester_scope_blocks_conversion(source):
    client, material, _ = source
    semester_id, course_id = create_course(source)
    with client.app.state.session_factory() as session:
        other = Workspace(name="他人空间")
        session.add(other)
        session.flush()
        foreign_semester = Semester(
            workspace_id=other.id,
            name="其他学期",
            start_date=date(2026, 9, 1),
            end_date=date(2027, 1, 1),
        )
        session.add(foreign_semester)
        session.flush()
        foreign_course = Course(
            workspace_id=other.id,
            semester_id=foreign_semester.id,
            name="其他课程",
        )
        session.add(foreign_course)
        session.commit()
        foreign_semester_id = foreign_semester.id
        foreign_course_id = foreign_course.id
    own = save(source, candidate_scope=CandidateScope(semester_id, course_id))
    with client.app.state.session_factory() as session:
        row = session.get(ExtractedItem, own.id)
        row.course_id = foreign_course_id
        session.commit()
    with pytest.raises(NotFoundError, match="课程不存在"):
        convert(source, own.id)
    with client.app.state.session_factory() as session:
        row = session.get(ExtractedItem, own.id)
        row.course_id = None
        row.semester_id = foreign_semester_id
        session.commit()
    with pytest.raises(NotFoundError, match="学期不存在"):
        convert(source, own.id)
    with client.app.state.session_factory() as session:
        repos = build_repositories(session)
        assert repos.rules.list(material["workspace_id"]) == []


def test_rules_api_lists_confirmed_rules_and_filters(client, source):
    """GET /api/v1/rules 只返回已转换的正式规则，按课程／学期过滤。"""
    semester_id, course_id = create_course(source)
    candidate = save(source, candidate_scope=CandidateScope(semester_id, course_id))
    assert client.get("/api/v1/rules").json() == []
    convert(source, candidate.id)

    listed = client.get("/api/v1/rules")
    assert listed.status_code == 200
    body = listed.json()
    assert len(body) == 1
    assert body[0]["title"] == QUOTE_TITLE
    assert body[0]["rule_text"] == QUOTE_RULE
    assert body[0]["course_id"] == course_id
    assert body[0]["semester_id"] == semester_id
    assert body[0]["source_state"] == "available"

    assert len(client.get(f"/api/v1/rules?course_id={course_id}").json()) == 1
    assert len(client.get(f"/api/v1/rules?semester_id={semester_id}").json()) == 1
    assert client.get("/api/v1/rules?course_id=999999").status_code == 404


def test_rules_api_rejects_foreign_workspace_course(client, source):
    """过滤条件指向其他空间的课程时按不存在处理，不泄露其他空间。"""
    with client.app.state.session_factory() as session:
        other = Workspace(name="他人空间")
        session.add(other)
        session.flush()
        foreign_semester = Semester(
            workspace_id=other.id,
            name="其他学期",
            start_date=date(2026, 9, 1),
            end_date=date(2027, 1, 1),
        )
        session.add(foreign_semester)
        session.flush()
        foreign_course = Course(
            workspace_id=other.id, semester_id=foreign_semester.id, name="其他课"
        )
        session.add(foreign_course)
        session.commit()
        foreign_course_id = foreign_course.id
    assert client.get(f"/api/v1/rules?course_id={foreign_course_id}").status_code == 404
    assert client.get("/api/v1/rules").json() == []
