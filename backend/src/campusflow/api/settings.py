"""个人时区与学习偏好 HTTP 路由。"""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from campusflow.api.deps import get_current_workspace, get_repositories
from campusflow.application import settings as use_cases
from campusflow.application.ports.repositories import Repositories, WorkspaceData

router = APIRouter(prefix="/settings", tags=["settings"])
CurrentWorkspace = Annotated[WorkspaceData, Depends(get_current_workspace)]
Repos = Annotated[Repositories, Depends(get_repositories)]


class SettingsUpdate(BaseModel):
    timezone: str = Field(min_length=1, max_length=64)
    daily_capacity_minutes: int = Field(ge=0, le=1440)
    break_minutes: int = Field(ge=0, le=240)
    buffer_minutes: int = Field(ge=0, le=1440)


class SettingsOut(SettingsUpdate):
    model_config = ConfigDict(from_attributes=True)
    workspace_id: int = Field(validation_alias="id")


@router.get("", response_model=SettingsOut)
def get_settings(workspace: CurrentWorkspace, repos: Repos) -> SettingsOut:
    return SettingsOut.model_validate(use_cases.get_settings(repos, workspace.id))


@router.put("", response_model=SettingsOut)
def update_settings(
    payload: SettingsUpdate, workspace: CurrentWorkspace, repos: Repos
) -> SettingsOut:
    return SettingsOut.model_validate(
        use_cases.update_settings(
            repos,
            workspace.id,
            timezone=payload.timezone,
            daily_capacity_minutes=payload.daily_capacity_minutes,
            break_minutes=payload.break_minutes,
            buffer_minutes=payload.buffer_minutes,
        )
    )
