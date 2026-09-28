"""固定日程与可用时间 HTTP 路由。"""

from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from campusflow.api.deps import get_default_workspace_id, get_repositories
from campusflow.api.schemas import (
    AvailabilitySlotCreate,
    AvailabilitySlotOut,
    FixedEventCreate,
    FixedEventOut,
    FixedEventUpdate,
)
from campusflow.application import schedule as use_cases
from campusflow.application.ports.repositories import Repositories

router = APIRouter(tags=["schedule"])

WorkspaceId = Annotated[int, Depends(get_default_workspace_id)]
Repos = Annotated[Repositories, Depends(get_repositories)]


@router.get("/fixed-events", response_model=list[FixedEventOut])
def list_fixed_events(workspace_id: WorkspaceId, repos: Repos) -> list[FixedEventOut]:
    return [
        FixedEventOut.model_validate(e) for e in use_cases.list_fixed_events(repos, workspace_id)
    ]


@router.post("/fixed-events", response_model=FixedEventOut, status_code=201)
def create_fixed_event(
    payload: FixedEventCreate, workspace_id: WorkspaceId, repos: Repos
) -> FixedEventOut:
    event = use_cases.create_fixed_event(
        repos,
        workspace_id,
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
def update_fixed_event(event_id: int, payload: FixedEventUpdate, repos: Repos) -> FixedEventOut:
    current = use_cases.get_fixed_event(repos, event_id)
    merged = replace(current, **payload.model_dump(exclude_unset=True))
    event = use_cases.update_fixed_event(
        repos,
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
def delete_fixed_event(event_id: int, repos: Repos) -> Response:
    use_cases.delete_fixed_event(repos, event_id)
    return Response(status_code=204)


@router.get("/availability-slots", response_model=list[AvailabilitySlotOut])
def list_availability_slots(workspace_id: WorkspaceId, repos: Repos) -> list[AvailabilitySlotOut]:
    return [
        AvailabilitySlotOut.model_validate(s)
        for s in use_cases.list_availability_slots(repos, workspace_id)
    ]


@router.post("/availability-slots", response_model=AvailabilitySlotOut, status_code=201)
def create_availability_slot(
    payload: AvailabilitySlotCreate, workspace_id: WorkspaceId, repos: Repos
) -> AvailabilitySlotOut:
    slot = use_cases.create_availability_slot(
        repos,
        workspace_id,
        day_of_week=payload.day_of_week,
        start_time=payload.start_time,
        end_time=payload.end_time,
    )
    return AvailabilitySlotOut.model_validate(slot)


@router.delete("/availability-slots/{slot_id}", status_code=204)
def delete_availability_slot(slot_id: int, repos: Repos) -> Response:
    use_cases.delete_availability_slot(repos, slot_id)
    return Response(status_code=204)
