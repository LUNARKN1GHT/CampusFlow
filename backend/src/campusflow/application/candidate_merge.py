"""N016：合并用户选定的候选，迁移时间块并保留来源、进度和历史。"""

import hashlib
import json
from dataclasses import dataclass

from campusflow.application.candidates import get_candidate
from campusflow.application.ports.candidates import MergeRecordData
from campusflow.application.ports.repositories import Repositories
from campusflow.domain.candidates import ReviewChange, validate_review_change
from campusflow.domain.errors import ConflictError, DomainError
from campusflow.domain.states import ReviewStatus, TaskProgress


@dataclass(frozen=True)
class MergeSourcePreview:
    candidate_id: int
    version_id: int
    title: str | None
    review_status: str
    formal_kind: str
    formal_id: int | None
    progress: str | None
    plan_block_count: int


@dataclass(frozen=True)
class MergePreview:
    target_candidate_id: int
    kind: str
    target_title: str | None
    target_formal_id: int | None
    target_progress: str | None
    kept_fields: dict
    sources: tuple[MergeSourcePreview, ...]
    token: str
    auto_merge: bool = False


@dataclass(frozen=True)
class MergeResult:
    records: tuple[MergeRecordData, ...]
    created: bool
    preview: MergePreview


def _fields(candidate) -> dict:
    return {
        field.name: field.value
        for field in candidate.effective_draft().fields
        if field.value is not None
    }


def _formal(repos: Repositories, candidate) -> tuple[str, int | None, str | None, int]:
    kind = candidate.draft.kind.value
    if kind == "task":
        item = repos.tasks.get_by_source_candidate(candidate.id)
        return (
            kind,
            item.id if item else None,
            item.progress.value if item else None,
            (_block_count(repos, candidate.workspace_id, item.id) if item else 0),
        )
    if kind == "event":
        item = repos.schedule.get_fixed_event_by_source_candidate(candidate.id)
        return kind, item.id if item else None, None, 0
    item = repos.rules.get_by_source_candidate(candidate.id)
    return kind, item.id if item else None, None, 0


def _block_count(repos: Repositories, workspace_id: int, task_id: int) -> int:
    return sum(
        1
        for draft in repos.plans.list_drafts(workspace_id)
        for block in repos.plans.list_blocks(draft.id)
        if block.task_id == task_id
    )


def preview_merge(
    repos: Repositories, workspace_id: int, target_candidate_id: int, source_ids: tuple[int, ...]
) -> MergePreview:
    if not source_ids:
        raise DomainError("请选择要合并的来源候选")
    if len(set(source_ids)) != len(source_ids) or target_candidate_id in source_ids:
        raise DomainError("来源候选不能重复，也不能包含目标候选")
    target = get_candidate(repos, workspace_id, target_candidate_id)
    sources = []
    for source_id in source_ids:
        source = get_candidate(repos, workspace_id, source_id)
        if source.draft.kind is not target.draft.kind:
            raise DomainError("只能合并同一类型的候选事项")
        for field in ("semester_id", "course_id", "class_name"):
            left = getattr(target.draft.scope, field)
            right = getattr(source.draft.scope, field)
            if left and right and left != right:
                raise DomainError("学期、课程或教学班不同，不能合并为同一事项")
        kind, formal_id, progress, block_count = _formal(repos, source)
        sources.append(
            MergeSourcePreview(
                candidate_id=source.id,
                version_id=source.source_version_id,
                title=_fields(source).get("title"),
                review_status=source.review_status.value,
                formal_kind=kind,
                formal_id=formal_id,
                progress=progress,
                plan_block_count=block_count,
            )
        )
    kind, target_formal_id, target_progress, _ = _formal(repos, target)
    token_source = {
        "target": target_candidate_id,
        "sources": [
            {
                "candidate_id": item.candidate_id,
                "version_id": item.version_id,
                "formal_id": item.formal_id,
                "progress": item.progress,
                "blocks": item.plan_block_count,
            }
            for item in sources
        ],
        "target_formal_id": target_formal_id,
        "target_progress": target_progress,
    }
    token = hashlib.sha256(
        json.dumps(token_source, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return MergePreview(
        target_candidate_id=target_candidate_id,
        kind=target.draft.kind.value,
        target_title=_fields(target).get("title"),
        target_formal_id=target_formal_id,
        target_progress=target_progress,
        kept_fields=_fields(target),
        sources=tuple(sources),
        token=token,
    )


def execute_merge(
    repos: Repositories,
    workspace_id: int,
    *,
    target_candidate_id: int,
    source_ids: tuple[int, ...],
    preview_token: str,
    reason: str,
    operator: str,
) -> MergeResult:
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1000:
        raise DomainError("请填写 1 至 1000 字的合并原因")
    preview = preview_merge(repos, workspace_id, target_candidate_id, source_ids)
    existing_records = [
        repos.candidates.find_merge(workspace_id, target_candidate_id, source_id)
        for source_id in source_ids
    ]
    if all(record is not None for record in existing_records):
        return MergeResult(tuple(existing_records), False, preview)
    if preview_token != preview.token:
        raise ConflictError("合并范围或进度已变化，请重新预览后再确认")
    target_kind, target_formal_id, target_progress, _ = _formal(
        repos, get_candidate(repos, workspace_id, target_candidate_id)
    )
    records = []
    created = False
    try:
        for source in preview.sources:
            existing = repos.candidates.find_merge(
                workspace_id, target_candidate_id, source.candidate_id
            )
            if existing is not None:
                records.append(existing)
                continue
            source_candidate = get_candidate(repos, workspace_id, source.candidate_id)
            if source_candidate.review_status is ReviewStatus.PENDING or (
                source_candidate.review_status is ReviewStatus.CONFIRMED
            ):
                change = ReviewChange(
                    source_candidate.review_status,
                    ReviewStatus.SUPERSEDED,
                    operator,
                    f"已合并到候选 #{target_candidate_id}：{reason.strip()}",
                )
                validate_review_change(change)
                repos.candidates.set_review_status(workspace_id, source.candidate_id, change)
            if (
                target_kind == "task"
                and source.formal_id is not None
                and target_formal_id is not None
            ):
                repos.plans.reassign_task(source.formal_id, target_formal_id)
                if target_progress == TaskProgress.NOT_STARTED.value and source.progress in {
                    TaskProgress.IN_PROGRESS.value,
                    TaskProgress.DONE.value,
                }:
                    repos.tasks.set_progress(
                        target_formal_id,
                        TaskProgress(source.progress),
                        f"合并自候选 #{source.candidate_id}",
                    )
                repos.tasks.set_progress(
                    source.formal_id,
                    TaskProgress.CANCELLED,
                    f"已合并到任务 #{target_formal_id}",
                )
            record = repos.candidates.create_merge(
                workspace_id,
                target_candidate_id=target_candidate_id,
                source_candidate_id=source.candidate_id,
                target_formal_kind=target_kind,
                target_formal_id=target_formal_id,
                source_formal_id=source.formal_id,
                preserved_progress={
                    "target_progress": target_progress,
                    "source_progress": source.progress,
                },
                source_snapshot={
                    "candidate_id": source.candidate_id,
                    "version_id": source.version_id,
                    "title": source.title,
                    "formal_id": source.formal_id,
                    "plan_block_count": source.plan_block_count,
                },
                reason=reason.strip(),
                operator=operator,
            )
            records.append(record)
            created = True
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        raise
    return MergeResult(tuple(records), created, preview)


def list_merges(repos: Repositories, workspace_id: int, candidate_id: int) -> list[MergeRecordData]:
    get_candidate(repos, workspace_id, candidate_id)
    return repos.candidates.list_merges(workspace_id, candidate_id)
