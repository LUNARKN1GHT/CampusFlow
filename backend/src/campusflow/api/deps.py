"""API 依赖：会话、仓储与当前工作空间。

W005：当前空间通过应用用例解析（get_or_create_default_workspace），
本层不直接操作 Workspace ORM 模型。
"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from campusflow.application import workspaces as workspace_use_cases
from campusflow.application.ports.embeddings import Embedder
from campusflow.application.ports.generation import TextGenerator
from campusflow.application.ports.indexing import IndexManifests
from campusflow.application.ports.queue import JobQueue
from campusflow.application.ports.repositories import Repositories, WorkspaceData
from campusflow.application.ports.version_relations import VersionRelations
from campusflow.core.config import Settings
from campusflow.domain.errors import DomainError
from campusflow.infrastructure.calendar.tokens import TokenCipher
from campusflow.infrastructure.calendar_store import SqlCalendarStore
from campusflow.infrastructure.index_manifests import SqlIndexManifests
from campusflow.infrastructure.repositories import build_repositories
from campusflow.infrastructure.version_relations import SqlVersionRelations
from campusflow.workers.embeddings import make_embedder
from campusflow.workers.generation import build_text_generator


def get_session(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as session:
        yield session


def get_repositories(session: Annotated[Session, Depends(get_session)]) -> Repositories:
    return build_repositories(session)


def get_version_relations(session: Annotated[Session, Depends(get_session)]) -> VersionRelations:
    return SqlVersionRelations(session)


def get_calendar_store(
    session: Annotated[Session, Depends(get_session)],
) -> SqlCalendarStore:
    return SqlCalendarStore(session)


def get_calendar_provider(request: Request):
    return request.app.state.calendar_provider


def get_token_cipher(request: Request) -> TokenCipher:
    return request.app.state.token_cipher


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_current_workspace(
    repos: Annotated[Repositories, Depends(get_repositories)],
) -> WorkspaceData:
    """当前会话的工作空间（MVP 单用户：默认空间，不存在时创建）。"""
    return workspace_use_cases.get_or_create_default_workspace(repos)


def get_index_manifests(
    session: Annotated[Session, Depends(get_session)],
) -> IndexManifests:
    """索引清单读取（Q004）：查询只在当前请求会话内进行，不写库。"""
    return SqlIndexManifests(session)


def get_job_queue(request: Request) -> JobQueue | None:
    """作业队列；不可用时返回 None，调用方按"投递失败"处理（J003/J004）。"""
    return getattr(request.app.state, "job_queue", None)


def get_embedder(request: Request) -> Embedder:
    """Q011：真实向量模型按请求装配；未配置密钥时给出明确业务错误。"""
    try:
        return make_embedder(request.app.state.settings)
    except ValueError as error:
        raise DomainError(str(error)) from None


def get_text_generator(request: Request) -> TextGenerator:
    try:
        return build_text_generator(request.app.state.settings)
    except ValueError as error:
        raise DomainError(str(error)) from None
