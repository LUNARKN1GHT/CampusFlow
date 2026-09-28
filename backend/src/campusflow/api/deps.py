"""API 依赖：会话、仓储与默认工作空间。"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from campusflow.application.ports.repositories import Repositories
from campusflow.infrastructure.db.models import Workspace
from campusflow.infrastructure.repositories import (
    SqlAlchemyCourseRepository,
    SqlAlchemyScheduleRepository,
    SqlAlchemySemesterRepository,
    SqlAlchemyTaskRepository,
    SqlAlchemyUnitOfWork,
)

DEFAULT_WORKSPACE_NAME = "默认工作空间"


def get_session(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as session:
        yield session


def get_repositories(session: Annotated[Session, Depends(get_session)]) -> Repositories:
    return Repositories(
        semesters=SqlAlchemySemesterRepository(session),
        courses=SqlAlchemyCourseRepository(session),
        tasks=SqlAlchemyTaskRepository(session),
        schedule=SqlAlchemyScheduleRepository(session),
        uow=SqlAlchemyUnitOfWork(session),
    )


def get_default_workspace_id(session: Annotated[Session, Depends(get_session)]) -> int:
    """MVP 单用户：返回默认工作空间 ID，不存在则创建并立即提交。"""
    workspace_id = session.scalar(select(Workspace.id).limit(1))
    if workspace_id is None:
        workspace = Workspace(name=DEFAULT_WORKSPACE_NAME)
        session.add(workspace)
        session.commit()
        return workspace.id
    return workspace_id
