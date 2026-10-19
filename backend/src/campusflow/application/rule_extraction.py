"""X005：课程规则候选提取，完整原文与条件分列，保持待核对。

模型输出先经 X002 结构契约校验，再逐条检查正文完整性与条件语句，只返回草稿。
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
from campusflow.domain.rule_extraction import (
    require_complete_sentence,
    require_effective_basis,
    require_full_rule,
)

RULE_SPEC = ExtractionSpec(
    CandidateKind.RULE,
    "提取实验准入、提交格式、考核办法等课程规定，不把单次作业或活动通知当作规则。"
    "每条规定独立一项。title 从规则正文摘取简短名称，不改写成新的断言。"
    "rule_text 必须复制所选 source_chunk 的完整正文，保留条件、否定、例外、范围和关联说明。"
    "同一片段有多条规定可以共用完整正文，用标题区分；不得只输出结论子句。"
    "conditions_text、scope_text、effective_text 分别列出条件、适用范围和生效时间的完整原文语句，"
    "不能截断逗号或分号连接的条件和例外，不改写、不推断；它们可以引用其他相关片段。"
    "未知信息用 null 和 missing_reason，不填无条件、所有人或立即生效。"
    "不把资料导入日期、发布日期或当前时间当作规则生效时间；不自动选择新版本覆盖旧规则。"
    "所有字段为原文出处，无规则时返回空 items。",
    (
        FieldDefinition("title", "规则名称原文，来自 rule_text", allow_missing=False),
        FieldDefinition(
            "rule_text", "支持该规则的完整原文片段，不删条件和例外", allow_missing=False
        ),
        FieldDefinition("conditions_text", "条件及例外的完整原文语句；未知留空"),
        FieldDefinition("scope_text", "适用课程、班级、对象等完整原文语句；未知留空"),
        FieldDefinition("effective_text", "生效/失效范围的完整原文语句；未声明留空，不自动换算"),
    ),
)


def _check_rule(draft: CandidateDraft, sources: dict[int, str]) -> CandidateDraft:
    """校验单条规则：字段保留自身证据原文，正文为片段全文，限定信息为完整句。"""
    fields = {f.name: f for f in draft.fields}
    generated = [
        ReviewReason(
            "rule_context_review",
            "完整规则及其条件、范围和生效信息仍须一并核对，不能只使用标题",
            "rule_text",
        )
    ]
    for field in draft.fields:
        if field.value is None:
            generated.append(
                ReviewReason(f"missing_{field.name}", field.missing_reason, field.name)
            )
            continue
        # 只用包含当前字段全文的证据检查，避免用另一条无关证据蒙混过关。
        texts = [
            sources[e.chunk_id]
            for e in field.evidence
            if e.chunk_id in sources and field.value in e.quote
        ]
        if not texts:
            raise InvalidModelOutput("规则字段必须保留自身证据中的原文，不能凭空改写")
        try:
            if field.name == "rule_text":
                require_full_rule(field.value, texts)
            elif field.name != "title":
                require_complete_sentence(field.value, texts)
                if field.name == "effective_text":
                    # 只引用发布时间不足以说明生效，必须有生效/失效等表述。
                    require_effective_basis(field.value)
        except DomainError as error:
            raise InvalidModelOutput(str(error)) from None
    # 标题必须能在正文中逐字找到，避免模型另造一个概括性名称。
    if fields["title"].value not in fields["rule_text"].value:
        raise InvalidModelOutput("规则标题必须出现在完整规则原文中")
    # 同 (code, field) 的本地原因覆盖模型原因，其余模型原因保留；随后整体校验。
    keys = {(r.code, r.field) for r in generated}
    reasons = generated + [r for r in draft.review_reasons if (r.code, r.field) not in keys]
    result = replace(draft, review_reasons=tuple(reasons))
    try:
        validate_candidate(result)
    except DomainError:
        raise InvalidModelOutput("规则核对信息无效或过多，未接受部分结果") from None
    return result


def extract_rule_drafts(
    repos: Repositories,
    workspace_id: int,
    material_id: int,
    version_no: int,
    generator: TextGenerator,
) -> list[CandidateDraft]:
    """先授权后生成，仅返回候选；正式规则保存/确认由后续用例处理。

    任一候选不合规都会中止本次提取，不会返回部分结果。
    """
    drafts = generate_for_version(
        repos, workspace_id, material_id, version_no, generator, RULE_SPEC
    )
    sources = {
        c.id: c.text for c in list_version_chunks(repos, workspace_id, material_id, version_no)
    }
    return [_check_rule(draft, sources) for draft in drafts]
