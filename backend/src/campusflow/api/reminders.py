"""应用内提醒 HTTP 路由（A010）。

系统没有独立推送进程：读取列表或摘要时按当前规则同步生成提醒。提醒只是
提示，不代表任务完成或冲突结论；没有提醒也不等于没有待办。
"""

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
    """读取前先按当前规则与窗口同步（幂等）；unread_only 只看已发送未读。"""
    statuses = (status,) if status is not None else None
    return [
        ReminderTriggerOut.model_validate(item)
        for item in use_cases.list_reminders(
            repos, workspace.id, unread_only=unread_only, statuses=statuses
        )
    ]


@router.get("/reminders/summary", response_model=ReminderSummaryOut)
def reminder_summary(workspace: CurrentWorkspace, repos: Repos) -> ReminderSummaryOut:
    """计数基于最近触发记录；固定 note 说明空提醒不代表没有待办或冲突。"""
    return ReminderSummaryOut.model_validate(use_cases.reminder_summary(repos, workspace.id))


@router.post("/reminders/{trigger_id}/action", response_model=ReminderActionResultOut)
def trigger_action(
    trigger_id: int,
    payload: ReminderActionIn,
    workspace: CurrentWorkspace,
    repos: Repos,
) -> ReminderActionResultOut:
    """read 仅标记已读，dismiss 表示忽略；都不改变任务进度、截止或计划。"""
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
    """task_id 为空创建默认规则；同一范围只允许一条，重复创建返回 409。"""
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
    """只更新提前量、重复、暂停与启用；请求里的 task_id 不生效，范围不能改。"""
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
    """删除任务级规则后该任务回退默认规则；删除默认规则后回退内置默认。"""
    use_cases.delete_rule(repos, workspace.id, rule_id)


def _rule_out(repos: Repositories, workspace_id: int, rule_id: int) -> ReminderRuleOut:
    # 用例只返回规则本身，is_default 等派生字段从列表视图补齐。
    for item in use_cases.list_rules(repos, workspace_id):
        if item["id"] == rule_id:
            return ReminderRuleOut.model_validate(item)
    raise DomainError("提醒规则不存在")
