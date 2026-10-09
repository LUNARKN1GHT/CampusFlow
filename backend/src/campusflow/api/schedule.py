"""固定日程与可用时间 HTTP 路由。"""

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
)
from campusflow.application import schedule as use_cases
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


@router.delete("/fixed-events/{event_id}", status_code=204)
def delete_fixed_event(event_id: int, workspace: CurrentWorkspace, repos: Repos) -> Response:
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
