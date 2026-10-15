"""X003：任务原文字段提取、耗时建议与截止信息完整性检查。"""

from dataclasses import replace

from campusflow.application.candidate_generation import (
    ExtractionSpec,
    FieldDefinition,
    InvalidModelOutput,
    generate_for_version,
)
from campusflow.application.ports.generation import TextGenerator
from campusflow.application.ports.repositories import Repositories
from campusflow.domain.candidates import (
    CandidateDraft,
    CandidateKind,
    FieldOrigin,
    ReviewReason,
    validate_candidate,
)
from campusflow.domain.deadline_completeness import deadline_review_reasons
from campusflow.domain.errors import DomainError

TASK_SPEC = ExtractionSpec(
    CandidateKind.TASK,
    "仅提取需要学生完成的任务，每个独立任务一条；不要把单纯活动通知或一般规则当作任务。"
    "任务标题、提交方式、产出物和截止时间均选取原文连续摘录，保留条件与限制，不改写。"
    "截止时间只保留原文，不换算相对日期，不补年份、时刻或时区。"
    "due_text 只包含当前任务的截止要求，不混入发布日期、学年、其他任务的日期。"
    "未找到字段时使用 null 并说明缺失；不得用另一任务的要求填补当前任务。"
    "耗时只提供可修改的建议，以正整数分钟表示并解释估计依据；无法估计则留空。"
    "没有任务时返回空 items。",
    (
        FieldDefinition("title", "任务名称的原文摘录", allow_missing=False),
        FieldDefinition("submission_method", "提交方式及其条件的原文摘录"),
        FieldDefinition("deliverable", "需要提交的产出物及其要求的原文摘录"),
        FieldDefinition("due_text", "完整截止要求原文，保留相对日期和条件，不换算"),
        FieldDefinition(
            "estimated_minutes",
            "可编辑耗时建议（正整数分钟），没有合理依据时留空",
            "integer",
            origins=(FieldOrigin.SUGGESTION,),
        ),
    ),
)


def check_task_fields(draft: CandidateDraft) -> CandidateDraft:
    """只接收 X002 按 TASK_SPEC 验证过的草稿，再增加任务专用检查。

    纯函数，不读取数据库，供提取入口与 X010 评测脚本共用。
    """
    # 核对原因由本地规则产生；模型不能用同名原因覆盖缺失/建议标记。
    generated: list[ReviewReason] = []
    for field in draft.fields:
        if field.value is None:
            reason = ReviewReason(f"missing_{field.name}", field.missing_reason, field.name)
            generated.append(reason)
        elif field.name == "estimated_minutes":
            if field.value <= 0:
                raise InvalidModelOutput("任务耗时建议必须是正整数分钟")
            # 无论模型说明如何措辞，保留由程序生成的建议标识。
            reason = ReviewReason(
                "estimated_duration", "耗时为可修改建议，不是课程规定", field.name
            )
            generated.append(reason)
        elif not any(field.value in evidence.quote for evidence in field.evidence):
            raise InvalidModelOutput("任务字段必须保留自身证据中的原文，不接受补写或改写")
    due_text = next(field.value for field in draft.fields if field.name == "due_text")
    generated.extend(deadline_review_reasons(due_text))
    keys = {(r.code, r.field) for r in generated}
    reasons = generated + [r for r in draft.review_reasons if (r.code, r.field) not in keys]
    result = replace(draft, review_reasons=tuple(reasons))
    try:
        validate_candidate(result)
    except DomainError:
        raise InvalidModelOutput("任务候选核对信息无效或过多，未接受部分结果") from None
    return result


def extract_task_drafts(
    repos: Repositories,
    workspace_id: int,
    material_id: int,
    version_no: int,
    generator: TextGenerator,
) -> list[CandidateDraft]:
    """显式调用的应用入口；只返回草稿，不写库、创建正式任务或自动排队。"""
    drafts = generate_for_version(
        repos, workspace_id, material_id, version_no, generator, TASK_SPEC
    )
    return [check_task_fields(draft) for draft in drafts]
