"""API 依赖：会话、仓储与当前工作空间。

W005：当前空间通过应用用例解析（get_or_create_default_workspace），
本层不直接操作 Workspace ORM 模型。
"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from campusflow.application import workspaces as workspace_use_cases
from campusflow.application.ports.repositories import Repositories, WorkspaceData
from campusflow.infrastructure.repositories import (
    SqlAlchemyCourseRepository,
    SqlAlchemyJobRepository,
    SqlAlchemyMaterialRepository,
    SqlAlchemyScheduleRepository,
    SqlAlchemySemesterRepository,
    SqlAlchemyTaskRepository,
    SqlAlchemyUnitOfWork,
    SqlAlchemyWorkspaceRepository,
)


def get_session(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as session:
        yield session


def get_repositories(session: Annotated[Session, Depends(get_session)]) -> Repositories:
    return Repositories(
        workspaces=SqlAlchemyWorkspaceRepository(session),
        semesters=SqlAlchemySemesterRepository(session),
        courses=SqlAlchemyCourseRepository(session),
        tasks=SqlAlchemyTaskRepository(session),
        schedule=SqlAlchemyScheduleRepository(session),
        materials=SqlAlchemyMaterialRepository(session),
        jobs=SqlAlchemyJobRepository(session),
        uow=SqlAlchemyUnitOfWork(session),
    )


def get_current_workspace(
    repos: Annotated[Repositories, Depends(get_repositories)],
) -> WorkspaceData:
    """当前会话的工作空间（MVP 单用户：默认空间，不存在时创建）。"""
    return workspace_use_cases.get_or_create_default_workspace(repos)
