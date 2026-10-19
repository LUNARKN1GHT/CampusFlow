"""X007：授权范围内的候选课程匹配，结果可保存但不自动绑定。

匹配严格按课程名和教学班；唯一结果也只是待核对建议，候选 scope 不会被改写。
"""

from dataclasses import replace

from campusflow.application.courses import get_course
from campusflow.application.materials import get_material, list_version_chunks
from campusflow.application.ports.repositories import Repositories
from campusflow.domain.candidates import (
    CandidateDraft,
    CandidateField,
    FieldOrigin,
    ReviewReason,
    validate_candidate,
)
from campusflow.domain.course_matching import CourseMatch, CourseOption, match_course
from campusflow.domain.errors import DomainError, NotFoundError

# 本用例重算的字段：先移除旧值再写入，避免重复字段或残留过期匹配。
_OWN_FIELDS = {"course_name_text", "class_name_text", "course_match"}
# 仅限制返回的选项数量；唯一性与计数始终使用完整集合。
_OPTION_LIMIT = 20


def match_candidate_course(
    repos: Repositories,
    workspace_id: int,
    material_id: int,
    version_no: int,
    draft: CandidateDraft,
    *,
    semester_id: int | None,
    course_name: CandidateField | None = None,
    class_name: CandidateField | None = None,
) -> CandidateDraft:
    """可信调用方选定学期并提供原文字段；不联网、写库或修改 scope。

    字段名分别是 course_name_text/class_name_text，必须为有证据的 document 文本。
    省略时复用草稿同名字段；没有输入则记录缺失。正式确认由后续核对用例负责。
    """
    material = get_material(repos, workspace_id, material_id)
    if material.archived:
        raise DomainError("资料已归档，不能匹配课程")
    chunks = {
        c.id: c.text for c in list_version_chunks(repos, workspace_id, material_id, version_no)
    }
    validate_candidate(draft)
    if semester_id is not None and (type(semester_id) is not int or semester_id < 1):
        raise DomainError("学期编号无效")
    original = {field.name: field for field in draft.fields}
    texts = []
    # 只接受约定名称的原文字段；课程名与教学班长度上限不同，超长直接拒绝。
    for name, supplied in (("course_name_text", course_name), ("class_name_text", class_name)):
        field = (
            supplied
            or original.get(name)
            or CandidateField(
                name, None, FieldOrigin.DOCUMENT, missing_reason="原文未明确，需人工核对"
            )
        )
        if field.name != name or field.origin != FieldOrigin.DOCUMENT:
            raise DomainError("课程及教学班输入必须是约定名称的原文字段")
        if field.value is not None and (
            not isinstance(field.value, str)
            or not field.value.strip()
            or len(field.value) > (200 if name == "course_name_text" else 100)
        ):
            raise DomainError("课程名或教学班必须是有效长度的原文文本")
        texts.append(field)
    checked = replace(
        draft,
        fields=tuple(f for f in draft.fields if f.name not in _OWN_FIELDS) + tuple(texts),
        review_reasons=tuple(r for r in draft.review_reasons if r.field != "course_match"),
    )
    validate_candidate(checked)
    # 输入草稿和新字段的证据都验证，旧匹配结果不能携带跨版本引用。
    for field in (*draft.fields, *texts):
        for evidence in field.evidence:
            source = chunks.get(evidence.chunk_id)
            if source is None or evidence.quote not in source:
                raise DomainError("证据不属于当前原文版本或摘录不存在")
    for field in texts:
        if field.value is not None and not any(field.value in e.quote for e in field.evidence):
            raise DomainError("课程名及教学班必须保留自身证据中的原文，不接受简称补写")

    # 检查已有范围，但不让预绑定课程把同名候选集合缩成唯一结果。
    scope_semesters = {s for s in (material.semester_id, draft.scope.semester_id) if s is not None}
    bound_ids = {c for c in (material.course_id, draft.scope.course_id) if c is not None}
    if len(bound_ids) > 1:
        raise DomainError("候选课程与资料已关联课程不一致")
    bound_course = None
    for cid in bound_ids:
        bound_course = get_course(repos, workspace_id, cid)
        scope_semesters.add(bound_course.semester_id)
    if len(scope_semesters) > 1:
        raise DomainError("候选学期、课程与资料范围不一致")
    # 资料、候选与已绑定课程声明的学期必须统一，且每个学期都属于当前空间。
    for sid in scope_semesters | ({semester_id} if semester_id is not None else set()):
        if type(sid) is not int or sid < 1:
            raise DomainError("学期编号无效")
        semester = repos.semesters.get(sid)
        if semester is None or semester.workspace_id != workspace_id:
            raise NotFoundError("学期不存在")
        if semester.archived:
            raise DomainError("学期已归档，不能匹配课程")
    if semester_id is not None and scope_semesters and semester_id not in scope_semesters:
        raise DomainError("所选学期与资料或候选范围不一致")

    if semester_id is None:
        result = CourseMatch("missing_semester", None, (), (), "尚未选定学期，不搜索其他学期的课程")
    else:
        catalog = [
            CourseOption(c.id, c.name, c.class_name, c.code, c.teacher)
            for c in repos.courses.list(workspace_id, semester_id)
            if c.workspace_id == workspace_id and c.semester_id == semester_id
        ]
        result = match_course(catalog, texts[0].value, texts[1].value)
        # 已有关联与原文冲突时不沿用已有绑定，也不悄悄改成另一门课。
        declared_classes = {c.strip() for c in (material.class_name, draft.scope.class_name) if c}
        suggested = next((c for c in catalog if c.id == result.suggested_course_id), None)
        if (
            len(declared_classes) > 1
            or suggested
            and declared_classes
            and (suggested.class_name or "").strip() not in declared_classes
            or texts[1].value
            and declared_classes
            and texts[1].value.strip() not in declared_classes
            or bound_course
            and texts[0].value
            and bound_course.name.strip() != texts[0].value.strip()
            or bound_course
            and texts[1].value
            and (bound_course.class_name or "").strip() != texts[1].value.strip()
        ):
            result = replace(
                result,
                status="scope_conflict",
                suggested_course_id=None,
                reason="原文与资料/候选已有课程或教学班范围冲突，请核对，不自动覆盖",
            )

    # 只限制展示数量；唯一性判定使用完整集合，不把被截断的列表误判成唯一。
    value = {
        "semester_id": semester_id,
        "status": result.status,
        "suggested_course_id": result.suggested_course_id,
        "match_count": len(result.matches),
        "option_count": len(result.options),
        "options_truncated": len(result.options) > _OPTION_LIMIT,
        "options": [
            {
                "id": c.id,
                "name": c.name,
                "class_name": c.class_name,
                "code": c.code,
                "teacher": c.teacher,
            }
            for c in result.options[:_OPTION_LIMIT]
        ],
    }
    evidence = tuple(dict.fromkeys(e for f in texts for e in f.evidence))
    # 无原文字段时结果只表示“需选择”，并非有来源的推断事实。
    origin = FieldOrigin.INFERRED if evidence else FieldOrigin.SUGGESTION
    # 始终追加待核对原因；草稿中同名字段的旧原因由本用例覆盖，不能标成已确认。
    output = replace(
        checked,
        fields=checked.fields
        + (
            CandidateField(
                "course_match",
                value,
                origin,
                evidence,
                explanation=result.reason,
            ),
        ),
        review_reasons=tuple(r for r in draft.review_reasons if r.field != "course_match")
        + (
            ReviewReason(
                "course_match_review", result.reason + "；匹配结果仍须人工确认", "course_match"
            ),
        ),
    )
    validate_candidate(output)
    return output
