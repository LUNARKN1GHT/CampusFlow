"""X004：固定日程候选提取。只返回待核对草稿，不创建正式日程。

活动描述、起止时间与名称必须来自同一段原文；提交截止不能充当活动时间。
"""

from dataclasses import replace

from campusflow.application.candidate_generation import (
    ExtractionSpec,
    FieldDefinition,
    InvalidModelOutput,
    generate_for_version,
)
from campusflow.application.materials import list_version_chunks
from campusflow.application.ports.generation import TextGenerator
from campusflow.application.ports.repositories import Repositories
from campusflow.domain.candidates import (
    CandidateDraft,
    CandidateKind,
    ReviewReason,
    validate_candidate,
)
from campusflow.domain.errors import DomainError
from campusflow.domain.event_extraction import (
    event_time_reasons,
    require_event_context,
    require_event_statement,
)

EVENT_SPEC = ExtractionSpec(
    CandidateKind.EVENT,
    "只提取要求在特定时间参加的固定活动，如考试、讲座、答辩、会议或实验课。"
    "截止前完成或提交的作业不是固定活动；报名截止也不是活动开始或结束。"
    "每个活动独立一条。所有字段选取连续原文，不补写、换算或拼接。"
    "activity_text 必须包含当前活动名称或举办说明及其时间原文，"
    "从通知中单独选取活动描述，不混入报名、提交或完成任务的句子。"
    "start_text 和 end_text 必须来自 activity_text；缺任一端则填 null 并写原因，"
    "不得按常见时长猜结束时间，不把活动起止区间填成作业截止。"
    "如果起止共用日期，保留结束端实际原文，不给它补写日期。"
    "地点及参加条件也必须保留自己的原文证据；未写条件不代表所有人都能参加。"
    "无固定活动时返回空 items。",
    (
        FieldDefinition("title", "活动名称原文", allow_missing=False),
        FieldDefinition(
            "activity_text", "与当前活动名称关联的活动描述及时间原文", allow_missing=False
        ),
        FieldDefinition("start_text", "开始时间原文；缺失留空，不填截止时间"),
        FieldDefinition("end_text", "结束时间原文；缺失留空，不推算时长"),
        FieldDefinition("location", "地点原文；未明确则留空"),
        FieldDefinition("participation_conditions", "参加对象、资格及条件原文；未明确则留空"),
    ),
)


def _check_event(draft: CandidateDraft, sources: dict[int, str]) -> CandidateDraft:
    """校验活动名称、描述与时间同源；缺失字段转为核对原因，不构造时间点。"""
    fields = {f.name: f for f in draft.fields}
    statement = fields["activity_text"].value
    generated = []
    for field in draft.fields:
        if field.value is None:
            generated.append(
                ReviewReason(f"missing_{field.name}", field.missing_reason, field.name)
            )
        elif not any(field.value in e.quote for e in field.evidence):
            raise InvalidModelOutput("活动字段必须保留对应证据原文，不能补写或改写")
    try:
        # 描述本身及其完整原句都不能混入提交、报名截止等非活动要求。
        require_event_statement(statement)
        require_event_context(
            statement,
            [
                sources[e.chunk_id]
                for e in fields["activity_text"].evidence
                if e.chunk_id in sources
            ],
        )
    except DomainError as error:
        raise InvalidModelOutput(str(error)) from None
    if fields["title"].value not in statement:
        raise InvalidModelOutput("活动名称必须出现在活动描述中，不能混用其他事项")
    # 起止时间必须来自当前活动描述，不能借用其他活动或作业截止的时间。
    for name in ("start_text", "end_text"):
        value = fields[name].value
        if value is not None:
            if value not in statement:
                raise InvalidModelOutput("起止时间必须来自当前活动描述，不能混用提交截止或其他活动")
            generated.extend(event_time_reasons(value, name))
    # 此处不构造时间点或执行日期比较，避免跨天和省略日期时擅自解释。
    keys = {(r.code, r.field) for r in generated}
    reasons = generated + [r for r in draft.review_reasons if (r.code, r.field) not in keys]
    result = replace(draft, review_reasons=tuple(reasons))
    try:
        validate_candidate(result)
    except DomainError:
        raise InvalidModelOutput("活动核对信息无效或过多，未接受部分结果") from None
    return result


def extract_event_drafts(
    repos: Repositories,
    workspace_id: int,
    material_id: int,
    version_no: int,
    generator: TextGenerator,
) -> list[CandidateDraft]:
    """先校验空间与版本再调用模型；只返回草稿，不写库或创建固定日程。"""
    drafts = generate_for_version(
        repos, workspace_id, material_id, version_no, generator, EVENT_SPEC
    )
    sources = {
        c.id: c.text for c in list_version_chunks(repos, workspace_id, material_id, version_no)
    }
    return [_check_event(draft, sources) for draft in drafts]
