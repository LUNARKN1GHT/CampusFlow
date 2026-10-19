"""M7 应用内提醒 HTTP 路由（A010）。"""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from campusflow.api.deps import get_current_workspace, get_repositories
from campusflow.application import reminders as use_cases
from campusflow.application.ports.repositories import Repositories, WorkspaceData
from campusflow.domain.errors import DomainError

router = APIRouter(tags=["reminders"])

CurrentWorkspace = Annotated[WorkspaceData, Depends(get_current_workspace)]
Repos = Annotated[Repositories, Depends(get_repositories)]


class ReminderRuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: int | None = Field(default=None, ge=1)
    advance_minutes: int = Field(default=use_cases.DEFAULT_ADVANCE_MINUTES, ge=0, le=10080)
    repeat_minutes: int | None = Field(default=None, ge=1, le=10080)
    paused: bool = False
    enabled: bool = True


class ReminderRuleOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int | None
    task_id: int | None
    advance_minutes: int
    repeat_minutes: int | None
    paused: bool
    enabled: bool
    version: int
    is_default: bool
    builtin: bool = False


class ReminderTriggerOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    task_id: int | None
    kind: str
    message: str
    status: str
    scheduled_for: datetime
    sent_at: datetime | None
    read_at: datetime | None
    payload: dict[str, Any]


class ReminderSummaryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unread: int
    sent: int
    pending: int
    dismissed: int
    cancelled: int
    note: str


class ReminderActionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["read", "dismiss"]


class ReminderActionResultOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    status: str


@router.get("/reminders", response_model=list[ReminderTriggerOut])
def list_reminders(
    workspace: CurrentWorkspace,
    repos: Repos,
    unread_only: Annotated[bool, Query()] = False,
    status: Annotated[Literal["pending", "sent", "dismissed", "cancelled"] | None, Query()] = None,
) -> list[ReminderTriggerOut]:
    statuses = (status,) if status is not None else None
    return [
        ReminderTriggerOut.model_validate(item)
        for item in use_cases.list_reminders(
            repos, workspace.id, unread_only=unread_only, statuses=statuses
        )
    ]


@router.get("/reminders/summary", response_model=ReminderSummaryOut)
def reminder_summary(workspace: CurrentWorkspace, repos: Repos) -> ReminderSummaryOut:
    return ReminderSummaryOut.model_validate(use_cases.reminder_summary(repos, workspace.id))


@router.post("/reminders/{trigger_id}/action", response_model=ReminderActionResultOut)
def trigger_action(
    trigger_id: int,
    payload: ReminderActionIn,
    workspace: CurrentWorkspace,
    repos: Repos,
) -> ReminderActionResultOut:
    return ReminderActionResultOut.model_validate(
        use_cases.set_trigger_action(repos, workspace.id, trigger_id, action=payload.action)
    )


@router.get("/reminders/rules", response_model=list[ReminderRuleOut])
def list_rules(workspace: CurrentWorkspace, repos: Repos) -> list[ReminderRuleOut]:
    return [
        ReminderRuleOut.model_validate(item) for item in use_cases.list_rules(repos, workspace.id)
    ]


@router.post("/reminders/rules", response_model=ReminderRuleOut, status_code=201)
def create_rule(
    payload: ReminderRuleIn, workspace: CurrentWorkspace, repos: Repos
) -> ReminderRuleOut:
    rule = use_cases.create_rule(
        repos,
        workspace.id,
        task_id=payload.task_id,
        advance_minutes=payload.advance_minutes,
        repeat_minutes=payload.repeat_minutes,
        paused=payload.paused,
        enabled=payload.enabled,
    )
    return _rule_out(repos, workspace.id, rule.id)


@router.patch("/reminders/rules/{rule_id}", response_model=ReminderRuleOut)
def update_rule(
    rule_id: int, payload: ReminderRuleIn, workspace: CurrentWorkspace, repos: Repos
) -> ReminderRuleOut:
    use_cases.update_rule(
        repos,
        workspace.id,
        rule_id,
        advance_minutes=payload.advance_minutes,
        repeat_minutes=payload.repeat_minutes,
        paused=payload.paused,
        enabled=payload.enabled,
    )
    return _rule_out(repos, workspace.id, rule_id)


@router.delete("/reminders/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int, workspace: CurrentWorkspace, repos: Repos) -> None:
    use_cases.delete_rule(repos, workspace.id, rule_id)


def _rule_out(repos: Repositories, workspace_id: int, rule_id: int) -> ReminderRuleOut:
    for item in use_cases.list_rules(repos, workspace_id):
        if item["id"] == rule_id:
            return ReminderRuleOut.model_validate(item)
    raise DomainError("提醒规则不存在")
