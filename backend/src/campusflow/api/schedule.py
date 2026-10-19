"""固定日程与可用时间 HTTP 路由。

所有读写限定当前工作空间。固定日程的时间按工作空间时区解释，
删除只影响内部记录；可用时段按星期描述，供计划排期使用。
"""

from dataclasses import replace
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from campusflow.api.deps import get_current_workspace, get_repositories
from campusflow.api.schemas import (
    AvailabilitySlotCreate,
    AvailabilitySlotOut,
    FixedEventCreate,
    FixedEventOccurrenceOut,
    FixedEventOut,
    FixedEventUpdate,
    SourceDetailOut,
)
from campusflow.application import schedule as use_cases
from campusflow.application import source_detail as source_use_cases
from campusflow.application.ports.repositories import Repositories, WorkspaceData

router = APIRouter(tags=["schedule"])

CurrentWorkspace = Annotated[WorkspaceData, Depends(get_current_workspace)]
Repos = Annotated[Repositories, Depends(get_repositories)]


@router.get("/fixed-events", response_model=list[FixedEventOut])
def list_fixed_events(
    workspace: CurrentWorkspace,
    repos: Repos,
    semester_id: Annotated[int | None, Query()] = None,
) -> list[FixedEventOut]:
    return [
        FixedEventOut.model_validate(e)
        for e in use_cases.list_fixed_events(repos, workspace.id, semester_id=semester_id)
    ]


@router.get("/fixed-events/occurrences", response_model=list[FixedEventOccurrenceOut])
def list_fixed_event_occurrences(
    workspace: CurrentWorkspace,
    repos: Repos,
    start_date: Annotated[date, Query()],
    end_date: Annotated[date, Query()],
    semester_id: Annotated[int | None, Query()] = None,
) -> list[FixedEventOccurrenceOut]:
    """把每周重复展开成闭区间内的实例；日期边界按工作空间时区解释。

    未来时刻落在 DST 缺失或歧义处直接报错，不会静默挪动固定日程。
    """
    return [
        FixedEventOccurrenceOut.model_validate(occurrence)
        for occurrence in use_cases.list_fixed_event_occurrences(
            repos, workspace.id, start_date, end_date, semester_id=semester_id
        )
    ]


@router.post("/fixed-events", response_model=FixedEventOut, status_code=201)
def create_fixed_event(
    payload: FixedEventCreate, workspace: CurrentWorkspace, repos: Repos
) -> FixedEventOut:
    """固定日程是排期先满足的确定时间；每周重复必须给出 repeat_until（含当天）。"""
    event = use_cases.create_fixed_event(
        repos,
        workspace.id,
        course_id=payload.course_id,
        title=payload.title,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        location=payload.location,
        recurrence=payload.recurrence,
        repeat_until=payload.repeat_until,
    )
    return FixedEventOut.model_validate(event)


@router.patch("/fixed-events/{event_id}", response_model=FixedEventOut)
def update_fixed_event(
    event_id: int, payload: FixedEventUpdate, workspace: CurrentWorkspace, repos: Repos
) -> FixedEventOut:
    # 未提交的字段保持原值；显式 null 只对可空字段生效，必填项由 schema 拒绝。
    current = use_cases.get_fixed_event(repos, workspace.id, event_id)
    merged = replace(current, **payload.model_dump(exclude_unset=True))
    event = use_cases.update_fixed_event(
        repos,
        workspace.id,
        event_id,
        course_id=merged.course_id,
        title=merged.title,
        starts_at=merged.starts_at,
        ends_at=merged.ends_at,
        location=merged.location,
        recurrence=merged.recurrence,
        repeat_until=merged.repeat_until,
    )
    return FixedEventOut.model_validate(event)


@router.get("/fixed-events/{event_id}/source", response_model=SourceDetailOut)
def get_fixed_event_source(
    event_id: int, workspace: CurrentWorkspace, repos: Repos
) -> SourceDetailOut:
    """来源已失效时记录仍保留并标记 source_state，只有可定位的引用算可验证。"""
    return SourceDetailOut.model_validate(
        source_use_cases.event_source_detail(repos, workspace.id, event_id)
    )


@router.delete("/fixed-events/{event_id}", status_code=204)
def delete_fixed_event(event_id: int, workspace: CurrentWorkspace, repos: Repos) -> Response:
    """只删除内部固定日程；已接受计划不随之改写，也不调用外部日历。"""
    use_cases.delete_fixed_event(repos, workspace.id, event_id)
    return Response(status_code=204)


@router.get("/availability-slots", response_model=list[AvailabilitySlotOut])
def list_availability_slots(workspace: CurrentWorkspace, repos: Repos) -> list[AvailabilitySlotOut]:
    return [
        AvailabilitySlotOut.model_validate(s)
        for s in use_cases.list_availability_slots(repos, workspace.id)
    ]


@router.post("/availability-slots", response_model=AvailabilitySlotOut, status_code=201)
def create_availability_slot(
    payload: AvailabilitySlotCreate, workspace: CurrentWorkspace, repos: Repos
) -> AvailabilitySlotOut:
    """day_of_week 以周一为 0；同日重叠拒绝，跨午夜需拆成两天录入。"""
    slot = use_cases.create_availability_slot(
        repos,
        workspace.id,
        day_of_week=payload.day_of_week,
        start_time=payload.start_time,
        end_time=payload.end_time,
    )
    return AvailabilitySlotOut.model_validate(slot)


@router.delete("/availability-slots/{slot_id}", status_code=204)
def delete_availability_slot(slot_id: int, workspace: CurrentWorkspace, repos: Repos) -> Response:
    use_cases.delete_availability_slot(repos, workspace.id, slot_id)
    return Response(status_code=204)
