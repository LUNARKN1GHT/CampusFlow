"""X006：为 X003 任务草稿附加可保存的日期换算结果与版本内证据。"""

import re
from dataclasses import replace

from campusflow.application.materials import get_material, list_version_chunks
from campusflow.application.ports.repositories import Repositories
from campusflow.domain.candidates import (
    CandidateDraft,
    CandidateField,
    CandidateKind,
    FieldEvidence,
    FieldOrigin,
    ReviewReason,
    validate_candidate,
)
from campusflow.domain.errors import DomainError
from campusflow.domain.relative_dates import (
    DateResolution,
    parse_date_anchor,
    resolve_relative_date,
)


def resolve_task_deadline(
    repos: Repositories,
    workspace_id: int,
    material_id: int,
    version_no: int,
    draft: CandidateDraft,
    *,
    source_timezone: str | None = None,
    publication_evidence: FieldEvidence | None = None,
    calendar_evidence: FieldEvidence | None = None,
) -> CandidateDraft:
    """显式可信调用，不联网、不写库；调用方再用 save_candidates 保存待核对草稿。

    依据由调用方选定当前版本内的完整声明；来源时区必须明确传入，不能来自模型猜测。
    不使用可变的资料元数据、导入时间或系统当前日期作为历史版本的日期依据。
    """
    material = get_material(repos, workspace_id, material_id)
    if material.archived:
        raise DomainError("资料已归档，不能换算日期")
    sources = {
        c.id: c.text for c in list_version_chunks(repos, workspace_id, material_id, version_no)
    }
    validate_candidate(draft)
    if draft.kind != CandidateKind.TASK:
        raise DomainError("日期换算入口只接受任务候选")
    if (draft.scope.semester_id, draft.scope.course_id, draft.scope.class_name) != (
        material.semester_id,
        material.course_id,
        material.class_name,
    ):
        raise DomainError("候选适用范围与当前资料不一致")
    fields = {f.name: f for f in draft.fields}
    due = fields.get("due_text")
    if due is None or due.origin != FieldOrigin.DOCUMENT:
        raise DomainError("缺少原文出处的截止描述")
    if due.value is not None and not isinstance(due.value, str):
        raise DomainError("截止描述必须是原文文本")

    def check_evidence(evidence: FieldEvidence) -> str:
        text = sources.get(evidence.chunk_id)
        if text is None or not evidence.quote or evidence.quote not in text:
            raise DomainError("日期证据不属于当前原文版本或摘录不存在")
        return text

    for field in draft.fields:
        for evidence in field.evidence:
            check_evidence(evidence)
    if due.value is not None and not any(due.value in e.quote for e in due.evidence):
        raise DomainError("截止描述必须保留自身证据中的原文")
    uncertain_context = False
    if due.value:
        # 引用存在不等于语义确定；检查截止所在原文语句，防止短摘录藏掉变更或条件。
        for evidence in due.evidence:
            for sentence in re.split(r"[。！？!?\n]", sources[evidence.chunk_id]):
                if due.value.rstrip("。!?！") in sentence and re.search(
                    r"取消|暂定|不再|不是|若|如果|另行|延期|改为|改到|除非|待定", sentence
                ):
                    uncertain_context = True
    anchors = []
    # 验证所有显式选定依据，不能忽略非法引用后继续换算。
    for evidence in (publication_evidence, calendar_evidence):
        if evidence is None:
            continue
        text = check_evidence(evidence)
        # 必须是完整声明，不能从“不是发布时间：...”等句子中截取肯定结论。
        if not any(
            (not text[: m.start()].strip() or text[: m.start()].rstrip(" \t\r")[-1] in "。!?！？\n")
            and (
                not text[m.end() :].strip()
                or evidence.quote[-1] in "。!?！？\n"
                or text[m.end() :].lstrip(" \t\r")[0] in "。!?！？\n"
            )
            for m in re.finditer(re.escape(evidence.quote), text)
        ):
            raise DomainError("日期依据须保留完整声明，不能截断上下文")
        anchors.append(evidence)

    semester = None
    if material.semester_id is not None:
        semester = repos.semesters.get(material.semester_id)
        if semester is None or semester.workspace_id != workspace_id or semester.archived:
            raise DomainError("适用学期不存在或已归档")
    try:
        publication = (
            parse_date_anchor(publication_evidence.quote, "publication", source_timezone)
            if publication_evidence
            else None
        )
        week_one = (
            parse_date_anchor(calendar_evidence.quote, "teaching_week", source_timezone)
            if calendar_evidence
            else None
        )
        result = resolve_relative_date(
            due.value,
            timezone=source_timezone,
            publication=publication,
            week_one=week_one,
            semester_start=semester.start_date if semester else None,
            semester_end=semester.end_date if semester else None,
        )
        if uncertain_context:
            result = DateResolution(reason="截止所在原文含条件、暂定或变更说明，需人工核对")
        if result.day and semester and not semester.start_date <= result.day <= semester.end_date:
            result = DateResolution(reason="换算日期超出资料适用学期，需核对来源与学期")
    except DomainError as error:
        result = DateResolution(reason=str(error))

    value = None
    if result.day or result.range_start:
        value = {
            "date": result.day.isoformat() if result.day else None,
            "time": result.clock.isoformat() if result.clock else None,
            "range_start": result.range_start.isoformat() if result.range_start else None,
            "range_end": result.range_end.isoformat() if result.range_end else None,
            "timezone": source_timezone,
            "anchor_date": result.anchor.isoformat(),
            "anchor_kind": result.anchor_kind,
            "rule": result.rule,
            "relation": result.relation,
        }
    evidence = tuple(dict.fromkeys((*due.evidence, *anchors)))
    resolution = CandidateField(
        "due_resolution",
        value,
        FieldOrigin.INFERRED,
        evidence,
        explanation=result.rule if value else None,
        missing_reason=result.reason if value is None else None,
    )
    reasons = [r for r in draft.review_reasons if r.field != "due_resolution"]
    reasons.append(
        ReviewReason(
            "date_conversion_review",
            "换算结果及依据仍须人工核对，不能自动确认截止时间",
            "due_resolution",
        )
    )
    if result.reason:
        reasons.append(ReviewReason("date_conversion_incomplete", result.reason, "due_resolution"))
    output = replace(
        draft,
        fields=tuple(f for f in draft.fields if f.name != "due_resolution") + (resolution,),
        review_reasons=tuple(reasons),
    )
    validate_candidate(output)
    return output
