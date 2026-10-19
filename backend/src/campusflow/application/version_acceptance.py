"""N014：用户接受字段差异，事务内标记旧候选失效并保存证据历史。

接受必须由用户显式选字段并填写原因；不会自动接受整份新版本。判定为
冲突或需核对时还要显式确认。同一组字段重复提交返回既有记录，不重复写。
"""

from dataclasses import dataclass

from campusflow.application.candidates import get_candidate
from campusflow.application.ports.repositories import Repositories
from campusflow.application.ports.version_relations import VersionAcceptanceData, VersionRelations
from campusflow.application.reminders import sync_reminders
from campusflow.application.version_applicability import assess_applicability
from campusflow.domain.candidate_diff import DiffStatus
from campusflow.domain.candidates import ReviewChange
from campusflow.domain.errors import DomainError
from campusflow.domain.states import ReviewStatus


@dataclass(frozen=True)
class AcceptanceResult:
    record: VersionAcceptanceData
    created: bool
    baseline_review_status: str


def _side(side) -> dict:
    return {
        "candidate_id": side.candidate_id,
        "source_version_id": side.source_version_id,
        "present": side.present,
        "value": side.value,
        "effective_value": side.effective_value,
        "origin": side.origin.value if side.origin else None,
        "effective_origin": side.effective_origin.value if side.effective_origin else None,
        "verifiable": side.verifiable,
        "evidence": [
            {
                "chunk_id": item.chunk_id,
                "quote": item.quote,
                "locator_type": item.locator_type,
                "locator_value": item.locator_value,
                "verifiable": item.verifiable,
            }
            for item in side.evidence
        ],
    }


def accept_change(
    repos: Repositories,
    store: VersionRelations,
    workspace_id: int,
    *,
    baseline_candidate_id: int,
    comparison_candidate_id: int,
    field_names: tuple[str, ...],
    reason: str,
    operator: str,
    acknowledge_conflict: bool = False,
) -> AcceptanceResult:
    """接受用户选定的字段差异，并把旧候选标记为已失效。

    字段必须确实出现在差异中且确有变化；判定为 conflict/requires_review 时
    需要显式确认。写入事务内锁工作空间、更新状态、保存逐字段证据快照并
    重算提醒，任一步失败整体回滚。
    """
    if not field_names:
        raise DomainError("请选择要接受的字段差异")
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1000:
        raise DomainError("请填写 1 至 1000 字的接受原因")
    names = tuple(sorted(set(field_names)))
    applicability = assess_applicability(
        repos, store, workspace_id, baseline_candidate_id, comparison_candidate_id
    )
    diff = applicability.diff
    by_name = {item.name: item for item in diff.fields}
    unknown = [name for name in names if name not in by_name]
    if unknown:
        raise DomainError("以下字段不在本次差异中：" + "、".join(unknown))
    unchanged = [name for name in names if by_name[name].status is DiffStatus.UNCHANGED]
    if unchanged:
        raise DomainError("以下字段没有变化，无需接受：" + "、".join(unchanged))
    # 非 applicable 时不能静默接受：先展示判定原因，由用户显式确认冲突。
    if applicability.status != "applicable" and not acknowledge_conflict:
        reasons = "；".join(item.message for item in applicability.reasons)
        raise DomainError(f"当前版本判定为{applicability.status}：{reasons}；请核对后显式确认冲突")

    existing = store.find_acceptance(
        workspace_id, baseline_candidate_id, comparison_candidate_id, names
    )
    # 幂等键是“基准候选 + 新候选 + 字段集合”，字段顺序不影响；重复提交不重写。
    if existing is not None:
        return AcceptanceResult(existing, False, existing.previous_review_status)

    baseline = get_candidate(repos, workspace_id, baseline_candidate_id)
    change = ReviewChange(
        baseline.review_status,
        ReviewStatus.SUPERSEDED,
        operator,
        reason.strip(),
    )
    from campusflow.domain.candidates import validate_review_change

    validate_review_change(change)
    evidence = tuple(
        {
            "name": name,
            "status": by_name[name].status.value,
            "left": _side(by_name[name].left),
            "right": _side(by_name[name].right),
        }
        for name in names
    )
    try:
        # 先锁工作空间再写状态与历史，避免并发接受产生两套有效记录。
        store.lock_workspace(workspace_id)
        repos.candidates.set_review_status(workspace_id, baseline_candidate_id, change)
        record = store.create_acceptance(
            workspace_id,
            baseline_candidate_id=baseline_candidate_id,
            comparison_candidate_id=comparison_candidate_id,
            baseline_version_id=diff.left.source_version_id,
            comparison_version_id=diff.right.source_version_id,
            accepted_fields=names,
            reason=reason.strip(),
            operator=operator,
            evidence_snapshot=evidence,
            previous_review_status=baseline.review_status.value,
        )
        # 通知变更接受后按仍有效的任务字段重算提醒，旧窗口不再保留
        sync_reminders(repos, workspace_id)
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise
    return AcceptanceResult(record, True, baseline.review_status.value)


def list_acceptances(
    store: VersionRelations, workspace_id: int, candidate_id: int
) -> list[VersionAcceptanceData]:
    return store.list_acceptances(workspace_id, candidate_id)
