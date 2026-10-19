"""X008：读取解析覆盖范围，并把"未覆盖"与"跨来源冲突"转成候选核对原因。

两件事都不自动改核对状态：候选仍以 pending 落库，只有用户能确认。
"""

from collections.abc import Iterable, Sequence
from dataclasses import replace

from campusflow.application.ports.candidates import CandidateData
from campusflow.application.ports.repositories import MaterialVersionData, Repositories
from campusflow.domain.candidates import (
    CandidateDraft,
    CandidateScope,
    ReviewReason,
    validate_candidate,
)
from campusflow.domain.errors import DomainError
from campusflow.domain.source_conflicts import (
    SOURCE_CONFLICT,
    ConflictEntry,
    conflict_reason,
    field_conflicts,
)
from campusflow.domain.source_coverage import UncoveredRange, coverage_review_reasons

COVERAGE_KEY = "coverage"

_MAX_REASONS = 64
_MAX_LOCATOR_TEXT = 120
_MAX_REASON_TEXT = 500


def scope_key(scope: CandidateScope) -> tuple[int | None, int | None, str | None]:
    """候选的适用范围内键：同键的候选才互相比较字段取值。"""
    return (scope.semester_id, scope.course_id, scope.class_name)


def stored_uncovered_ranges(version: MaterialVersionData) -> tuple[UncoveredRange, ...]:
    """从版本元数据读回结构化覆盖信息。

    缺失或格式不符合预期时按"未报告未覆盖范围"处理，不猜测、不从展示文本反解。
    """
    raw = version.source_metadata.get(COVERAGE_KEY)
    if not isinstance(raw, dict):
        return ()
    failures = raw.get("failures")
    if not isinstance(failures, list):
        return ()
    ranges = []
    for item in failures:
        if not isinstance(item, dict):
            continue
        locator_type = item.get("locator_type")
        reason = item.get("reason")
        if not isinstance(locator_type, str) or not isinstance(reason, str) or not reason.strip():
            continue
        value = item.get("locator_value")
        ranges.append(
            UncoveredRange(
                locator_type[:_MAX_LOCATOR_TEXT],
                "" if value is None else str(value)[:_MAX_LOCATOR_TEXT],
                reason[:_MAX_REASON_TEXT],
            )
        )
    return tuple(ranges)


def _append_reasons(draft: CandidateDraft, reasons: Sequence[ReviewReason]) -> CandidateDraft:
    """幂等追加核对原因；已达上限时按顺序截断，不因原因过多让整批提取失败。"""
    existing = {(reason.code, reason.field) for reason in draft.review_reasons}
    added = [reason for reason in reasons if (reason.code, reason.field) not in existing]
    room = _MAX_REASONS - len(draft.review_reasons)
    if not added or room <= 0:
        return draft
    result = replace(draft, review_reasons=draft.review_reasons + tuple(added[:room]))
    try:
        validate_candidate(result)
    except DomainError:
        return draft
    return result


def with_coverage_reasons(
    drafts: Sequence[CandidateDraft], ranges: Sequence[UncoveredRange]
) -> list[CandidateDraft]:
    """未覆盖范围非空时，本批每条候选都带上同一候选级核对原因。

    原因只影响待核对提示，不改变字段取值与核对状态；截断来源的候选
    仍以 pending 落库，由用户核对原文后决定。
    """
    reasons = coverage_review_reasons(ranges)
    if not reasons:
        return list(drafts)
    return [_append_reasons(draft, reasons) for draft in drafts]


def conflict_entries_of(candidates: Iterable[CandidateData]) -> list[ConflictEntry]:
    """把候选的每个字段取值摊平成可比对的条目；来源键标明来自哪个资料版本。"""
    entries = []
    for candidate in candidates:
        origin_key = f"来源版本 #{candidate.source_version_id}"
        for field in candidate.draft.fields:
            entries.append(
                ConflictEntry(
                    candidate.id,
                    origin_key,
                    candidate.draft.kind,
                    scope_key(candidate.draft.scope),
                    field.name,
                    field.value,
                )
            )
    return entries


def refresh_conflict_reasons(
    repos: Repositories,
    workspace_id: int,
    scopes: Iterable[tuple[int | None, int | None, str | None]],
) -> int:
    """重算并补齐/清理每个适用范围内的冲突原因，返回被改动的候选数。

    先移除旧的冲突原因再按当前取值重新判定：来源被删除或被修正后，过期的冲突
    提示必须消失，否则用户会核对一个已经不存在的第二个取值。
    不提交事务，由调用方与候选写入一起提交；其他核对原因原样保留。
    """
    updated = 0
    for scope in dict.fromkeys(scopes):
        candidates = repos.candidates.list_by_scope(workspace_id, *scope)
        desired: dict[int, list[ReviewReason]] = {}
        for conflict in field_conflicts(conflict_entries_of(candidates)):
            reason = conflict_reason(conflict)
            for entry in conflict.entries:
                desired.setdefault(entry.candidate_id, []).append(reason)
        for candidate in candidates:
            # 只替换本期重算的冲突原因，保留覆盖不足等由其他流程写入的原因。
            kept = [r for r in candidate.draft.review_reasons if r.code != SOURCE_CONFLICT]
            wanted = (kept + desired.get(candidate.id, []))[:_MAX_REASONS]
            if wanted == list(candidate.draft.review_reasons):
                continue
            repos.candidates.set_review_reasons(workspace_id, candidate.id, tuple(wanted))
            updated += 1
    return updated
