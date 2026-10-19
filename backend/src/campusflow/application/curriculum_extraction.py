"""C003：从培养方案资料提取课程与三类关系候选，保持待核对。

只返回候选草稿；调用方显式保存后仍为 pending。无法唯一解释的关系不会直接
成为正式依赖，缺课程、未知学期或无法识别的类型都保留核对原因。
"""

import re
from dataclasses import replace

from campusflow.application.candidate_generation import (
    ExtractionSpec,
    FieldDefinition,
    InvalidModelOutput,
    generate_for_version,
)
from campusflow.application.course_paths import get_plan
from campusflow.application.materials import list_version_chunks
from campusflow.application.ports.generation import TextGenerator
from campusflow.application.ports.repositories import Repositories
from campusflow.domain.candidates import (
    CandidateDraft,
    CandidateField,
    CandidateKind,
    FieldOrigin,
    ReviewReason,
    validate_candidate,
)
from campusflow.domain.course_paths import RelationType, normalize_course_name
from campusflow.domain.errors import DomainError

COURSE_SPEC = ExtractionSpec(
    CandidateKind.CURRICULUM_COURSE,
    "提取培养方案中的课程条目。每门课程独立一项，课程名称必须逐字来自原文片段。"
    "credits 只填写原文明确给出的数值，未知填 null 并写 missing_reason；不得按常识补学分。"
    "offered_terms_text 复制说明开课学期的原文，如“秋季”“第 1 学期”“每学期”，未说明填 null。"
    "course_code 只填原文出现的课程编号，不猜测。无课程时返回空 items。",
    (
        FieldDefinition("course_name", "课程正式名称原文", allow_missing=False),
        FieldDefinition("course_code", "课程编号原文"),
        FieldDefinition("credits", "原文学分数值", "number"),
        FieldDefinition("offered_terms_text", "开课学期说明原文"),
    ),
)

RELATION_SPEC = ExtractionSpec(
    CandidateKind.COURSE_RELATION,
    "提取课程之间的先修、并修和建议先学关系。每条形如“学习 B 前须先修 A”时，"
    "from_course_name 填 A，to_course_name 填 B。relation_type 只填写原文表达："
    "强制先修写“强制先修”，必须同时修读写“并修”，仅建议提前学习写“建议先学”。"
    "不得把单次作业、考试安排或同时开课当作课程关系，不得根据课程顺序推测关系。"
    "课程名称必须逐字来自原文片段，无法确定的关系仍要保留原文并说明疑点。",
    (
        FieldDefinition("from_course_name", "前置/建议课程名称原文", allow_missing=False),
        FieldDefinition("to_course_name", "受约束课程名称原文", allow_missing=False),
        FieldDefinition(
            "relation_type", "关系类型原文：强制先修/并修/建议先学", allow_missing=False
        ),
        FieldDefinition("condition_text", "关系成立条件的完整原文语句"),
    ),
)

_TERM_PATTERNS = (
    (re.compile(r"秋|秋季|上学期|第\s*1\s*学期|第一学期|fall|autumn", re.IGNORECASE), "fall"),
    (re.compile(r"春|春季|下学期|第\s*2\s*学期|第二学期|spring", re.IGNORECASE), "spring"),
    (re.compile(r"夏|暑期|夏季|summer", re.IGNORECASE), "summer"),
    (re.compile(r"冬|冬季|winter", re.IGNORECASE), "winter"),
)

_RELATION_ALIASES = {
    "prerequisite": RelationType.PREREQUISITE,
    "强制先修": RelationType.PREREQUISITE,
    "先修": RelationType.PREREQUISITE,
    "必修前置": RelationType.PREREQUISITE,
    "corequisite": RelationType.COREQUISITE,
    "并修": RelationType.COREQUISITE,
    "同时修读": RelationType.COREQUISITE,
    "同修": RelationType.COREQUISITE,
    "recommended": RelationType.RECOMMENDED,
    "建议先学": RelationType.RECOMMENDED,
    "建议": RelationType.RECOMMENDED,
    "推荐先学": RelationType.RECOMMENDED,
}


def _terms_from_text(value: str) -> list[str]:
    terms = []
    for pattern, term in _TERM_PATTERNS:
        if pattern.search(value) and term not in terms:
            terms.append(term)
    return terms


def _evidence_texts(field: CandidateField, sources: dict[int, str]) -> list[str]:
    return [
        sources[evidence.chunk_id]
        for evidence in field.evidence
        if evidence.chunk_id in sources
        and evidence.quote in sources[evidence.chunk_id]
    ]


def _check_course(
    draft: CandidateDraft, sources: dict[int, str], plan_course_names: set[str]
) -> CandidateDraft:
    by_name = {field.name: field for field in draft.fields}
    generated: list[ReviewReason] = [
        ReviewReason(
            "curriculum_course_review",
            "课程、学分与开课学期仍须人工核对，确认后才写入方案课程",
            "course_name",
        )
    ]
    for field in draft.fields:
        if field.value is None:
            generated.append(
                ReviewReason(f"missing_{field.name}", field.missing_reason, field.name)
            )
            continue
        if not _evidence_texts(field, sources):
            raise InvalidModelOutput("课程字段必须保留自身证据中的原文")
    name_field = by_name["course_name"]
    if not isinstance(name_field.value, str):
        raise InvalidModelOutput("课程名称必须是原文文本")
    if not any(
        name_field.value in text for text in _evidence_texts(name_field, sources)
    ):
        raise InvalidModelOutput("课程名称必须逐字出现在证据中")
    credits = by_name["credits"].value
    if credits is not None and (type(credits) not in (int, float) or credits < 0):
        raise InvalidModelOutput("学分必须是非负数")
    if normalize_course_name(name_field.value) not in plan_course_names:
        generated.append(
            ReviewReason(
                "curriculum_course_unmatched",
                "方案内尚未存在同名课程；确认后将新建，或先核对是否与已有课程重复",
                "course_name",
            )
        )
    fields = list(draft.fields)
    term_field = by_name["offered_terms_text"]
    if isinstance(term_field.value, str):
        terms = _terms_from_text(term_field.value)
        if not terms:
            generated.append(
                ReviewReason(
                    "offered_terms_unknown",
                    "未识别出明确开课季节，排学期时按未知处理",
                    "offered_terms_text",
                )
            )
        else:
            fields.append(
                CandidateField(
                    "offered_terms",
                    terms,
                    FieldOrigin.INFERRED,
                    term_field.evidence,
                    explanation="按原文开课学期说明归一化为 fall/spring/summer/winter",
                )
            )
    result = replace(
        draft,
        fields=tuple(fields),
        review_reasons=tuple(generated),
    )
    try:
        validate_candidate(result)
    except DomainError:
        raise InvalidModelOutput("课程候选核对信息无效，未接受部分结果") from None
    return result


def _normalize_relation_type(value: object) -> RelationType:
    if not isinstance(value, str):
        raise InvalidModelOutput("关系类型必须是原文文本")
    key = value.strip().casefold()
    relation = _RELATION_ALIASES.get(key)
    if relation is None:
        raise InvalidModelOutput("关系类型无法确定为强制先修、并修或建议先学")
    return relation


def _check_relation(
    draft: CandidateDraft, sources: dict[int, str], plan_course_names: set[str]
) -> CandidateDraft:
    by_name = {field.name: field for field in draft.fields}
    generated: list[ReviewReason] = [
        ReviewReason(
            "course_relation_review",
            "课程关系须人工核对，确认后才写入正式关系",
            "relation_type",
        )
    ]
    for field in draft.fields:
        if field.value is None:
            generated.append(
                ReviewReason(f"missing_{field.name}", field.missing_reason, field.name)
            )
            continue
        if not _evidence_texts(field, sources):
            raise InvalidModelOutput("关系字段必须保留自身证据中的原文")
    _normalize_relation_type(by_name["relation_type"].value)
    for name in ("from_course_name", "to_course_name"):
        value = by_name[name].value
        if not isinstance(value, str) or not any(
            value in text for text in _evidence_texts(by_name[name], sources)
        ):
            raise InvalidModelOutput("关系中的课程名称必须逐字出现在证据中")
        if normalize_course_name(value) not in plan_course_names:
            generated.append(
                ReviewReason(
                    "relation_course_unmatched",
                    f"课程“{value.strip()}”尚未在方案内唯一匹配，确认前不能形成正式依赖",
                    name,
                )
            )
    result = replace(draft, review_reasons=tuple(generated))
    try:
        validate_candidate(result)
    except DomainError:
        raise InvalidModelOutput("关系候选核对信息无效，未接受部分结果") from None
    return result


def extract_curriculum_drafts(
    repos: Repositories,
    workspace_id: int,
    plan_id: int,
    material_id: int,
    version_no: int,
    generator: TextGenerator,
    *,
    include_courses: bool = True,
    include_relations: bool = True,
) -> list[CandidateDraft]:
    """先授权后生成；返回尚未保存的课程与关系候选。"""
    get_plan(repos, workspace_id, plan_id)
    if not include_courses and not include_relations:
        raise DomainError("至少选择一种提取类型")
    sources = {
        chunk.id: chunk.text
        for chunk in list_version_chunks(repos, workspace_id, material_id, version_no)
    }
    plan_names = {
        normalize_course_name(course.name)
        for course in repos.paths.list_courses(plan_id)
    }
    drafts: list[CandidateDraft] = []
    if include_courses:
        for draft in generate_for_version(
            repos,
            workspace_id,
            material_id,
            version_no,
            generator,
            COURSE_SPEC,
            curriculum_plan_id=plan_id,
        ):
            drafts.append(_check_course(draft, sources, plan_names))
    if include_relations:
        for draft in generate_for_version(
            repos,
            workspace_id,
            material_id,
            version_no,
            generator,
            RELATION_SPEC,
            curriculum_plan_id=plan_id,
        ):
            drafts.append(_check_relation(draft, sources, plan_names))
    return drafts


__all__ = ["COURSE_SPEC", "RELATION_SPEC", "extract_curriculum_drafts"]
