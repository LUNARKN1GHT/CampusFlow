"""N013：列出通知变更可能影响的正式事项与计划时间块；只读、不执行调整。

影响范围来自基准候选（旧版本）已经生成的正式事项；已完成任务与锁定时间块
照常列出，提醒与外部日历明确标为未接入，不伪装已同步。
"""

from dataclasses import dataclass

from campusflow.application.candidate_diff import CandidateDiff, compare_candidates
from campusflow.application.ports.repositories import Repositories
from campusflow.domain.states import TaskProgress


@dataclass(frozen=True)
class ImpactedBlock:
    id: int
    draft_id: int
    draft_status: str
    title: str
    starts_at: str
    ends_at: str
    locked: bool


@dataclass(frozen=True)
class ImpactedTask:
    id: int
    title: str
    progress: str
    completed: bool
    due_date: str | None
    due_time: str | None
    source_state: str
    plan_blocks: tuple[ImpactedBlock, ...]


@dataclass(frozen=True)
class ImpactedEvent:
    id: int
    title: str
    starts_at: str
    ends_at: str
    location: str | None


@dataclass(frozen=True)
class ImpactedRule:
    id: int
    title: str


@dataclass(frozen=True)
class ChangeImpact:
    diff: CandidateDiff
    tasks: tuple[ImpactedTask, ...]
    fixed_events: tuple[ImpactedEvent, ...]
    rules: tuple[ImpactedRule, ...]
    unsupported: tuple[dict, ...]
    summary: dict


def _task_impact(repos: Repositories, workspace_id: int, task) -> ImpactedTask:
    # 已完成任务和锁定时间块也要列出：变更影响不能被“已完成”或“已锁定”掩盖。
    drafts = repos.plans.list_drafts(workspace_id)
    blocks: list[ImpactedBlock] = []
    for draft in drafts:
        for block in repos.plans.list_blocks(draft.id):
            if block.task_id != task.id:
                continue
            blocks.append(
                ImpactedBlock(
                    id=block.id,
                    draft_id=block.draft_id,
                    draft_status=draft.status,
                    title=block.title,
                    starts_at=block.starts_at.isoformat(),
                    ends_at=block.ends_at.isoformat(),
                    locked=block.locked,
                )
            )
    return ImpactedTask(
        id=task.id,
        title=task.title,
        progress=task.progress.value,
        completed=task.progress is TaskProgress.DONE,
        due_date=task.due_date.isoformat() if task.due_date else None,
        due_time=task.due_time.isoformat() if task.due_time else None,
        source_state=task.source_state,
        plan_blocks=tuple(blocks),
    )


def collect_impact(
    repos: Repositories,
    workspace_id: int,
    baseline_candidate_id: int,
    comparison_candidate_id: int,
) -> ChangeImpact:
    """对比两个候选并列出基准侧已生成事项的影响；不修改计划或任务。

    只查基准（旧）候选的来源关联：新版本尚未被接受，不能提前把它的
    正式事项当成现有安排。
    """
    diff = compare_candidates(repos, workspace_id, baseline_candidate_id, comparison_candidate_id)
    tasks = []
    task = repos.tasks.get_by_source_candidate(baseline_candidate_id)
    if task is not None:
        tasks.append(_task_impact(repos, workspace_id, task))

    events = []
    event = repos.schedule.get_fixed_event_by_source_candidate(baseline_candidate_id)
    if event is not None:
        events.append(
            ImpactedEvent(
                id=event.id,
                title=event.title,
                starts_at=event.starts_at.isoformat(),
                ends_at=event.ends_at.isoformat(),
                location=event.location,
            )
        )

    rules = []
    rule = repos.rules.get_by_source_candidate(baseline_candidate_id)
    if rule is not None:
        rules.append(ImpactedRule(id=rule.id, title=rule.title))

    return ChangeImpact(
        diff=diff,
        tasks=tuple(tasks),
        fixed_events=tuple(events),
        rules=tuple(rules),
        # 首期没有应用内提醒和外部日历连接，必须明确未同步，不能静默省略。
        unsupported=(
            {
                "code": "reminders",
                "message": "应用内提醒尚未接入，本次不调整或声称已重排提醒。",
            },
            {
                "code": "external_calendar",
                "message": "外部日历尚未接入，本次不写入或声称已同步。",
            },
        ),
        summary={
            "task_count": len(tasks),
            "completed_task_count": sum(1 for item in tasks if item.completed),
            "locked_block_count": sum(
                1 for item in tasks for block in item.plan_blocks if block.locked
            ),
            "fixed_event_count": len(events),
            "rule_count": len(rules),
        },
    )
