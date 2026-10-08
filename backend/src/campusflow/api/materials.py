"""资料导入 HTTP 路由（D004 起）。"""

from dataclasses import replace
from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from campusflow.api.deps import get_current_workspace, get_repositories
from campusflow.application import materials as use_cases
from campusflow.application.ports.parsers import DocumentParser
from campusflow.application.ports.repositories import Repositories, WorkspaceData
from campusflow.application.ports.storage import FileStorage
from campusflow.domain.states import MaterialSourceType, MaterialStatus
from campusflow.infrastructure.parsers.text import TextDocumentParser

router = APIRouter(prefix="/materials", tags=["materials"])

CurrentWorkspace = Annotated[WorkspaceData, Depends(get_current_workspace)]
Repos = Annotated[Repositories, Depends(get_repositories)]

LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def get_file_storage(request: Request) -> FileStorage:
    return request.app.state.file_storage


Storage = Annotated[FileStorage, Depends(get_file_storage)]


def get_text_parser() -> DocumentParser:
    return TextDocumentParser()


TextParser = Annotated[DocumentParser, Depends(get_text_parser)]


class TextImportIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1)
    publisher: str | None = Field(default=None, max_length=200)
    published_at: datetime | None = None
    semester_id: int | None = None
    course_id: int | None = None
    class_name: str | None = Field(default=None, max_length=100)

    @field_validator("published_at")
    @classmethod
    def assume_local(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo is not None else value.replace(tzinfo=LOCAL_TZ)


class MaterialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    semester_id: int | None
    course_id: int | None
    class_name: str | None
    title: str
    publisher: str | None
    published_at: datetime | None
    source_type: MaterialSourceType
    source_url: str | None
    status: MaterialStatus
    archived: bool
    imported_at: datetime


class SourceChunkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version_id: int
    seq: int
    locator_type: str
    locator_value: str
    text: str


class TextImportOut(BaseModel):
    material: MaterialOut
    chunks: list[SourceChunkOut]


class MaterialMetadataUpdate(BaseModel):
    """元数据修订（D005）：全部字段可选；传入 null 表示清空该字段。"""

    title: str | None = Field(default=None, min_length=1, max_length=300)
    publisher: str | None = Field(default=None, max_length=200)
    published_at: datetime | None = None
    source_url: str | None = Field(default=None, max_length=500)
    semester_id: int | None = None
    course_id: int | None = None
    class_name: str | None = Field(default=None, max_length=100)

    @field_validator("published_at")
    @classmethod
    def assume_local(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo is not None else value.replace(tzinfo=LOCAL_TZ)


@router.post("/text", response_model=TextImportOut, status_code=201)
def import_text(
    payload: TextImportIn,
    workspace: CurrentWorkspace,
    repos: Repos,
    storage: Storage,
    text_parser: TextParser,
) -> TextImportOut:
    material, chunks = use_cases.import_pasted_text(
        repos,
        storage,
        workspace.id,
        title=payload.title,
        content=payload.content,
        publisher=payload.publisher,
        published_at=payload.published_at,
        semester_id=payload.semester_id,
        course_id=payload.course_id,
        class_name=payload.class_name,
        text_parser=text_parser,
    )
    return TextImportOut(
        material=MaterialOut.model_validate(material),
        chunks=[SourceChunkOut.model_validate(c) for c in chunks],
    )


@router.get("", response_model=list[MaterialOut])
def list_materials(
    workspace: CurrentWorkspace,
    repos: Repos,
    semester_id: Annotated[int | None, Query()] = None,
    course_id: Annotated[int | None, Query()] = None,
    source_type: Annotated[MaterialSourceType | None, Query()] = None,
    status: Annotated[MaterialStatus | None, Query()] = None,
    include_archived: Annotated[bool, Query()] = False,
) -> list[MaterialOut]:
    return [
        MaterialOut.model_validate(m)
        for m in use_cases.list_materials(
            repos,
            workspace.id,
            semester_id=semester_id,
            course_id=course_id,
            source_type=source_type,
            status=status,
            include_archived=include_archived,
        )
    ]


@router.get("/{material_id}", response_model=MaterialOut)
def get_material(material_id: int, workspace: CurrentWorkspace, repos: Repos) -> MaterialOut:
    return MaterialOut.model_validate(use_cases.get_material(repos, workspace.id, material_id))


@router.patch("/{material_id}", response_model=MaterialOut)
def update_material(
    material_id: int, payload: MaterialMetadataUpdate, workspace: CurrentWorkspace, repos: Repos
) -> MaterialOut:
    current = use_cases.get_material(repos, workspace.id, material_id)
    merged = replace(current, **payload.model_dump(exclude_unset=True))
    material = use_cases.update_material_metadata(
        repos,
        workspace.id,
        material_id,
        title=merged.title,
        publisher=merged.publisher,
        published_at=merged.published_at,
        source_url=merged.source_url,
        semester_id=merged.semester_id,
        course_id=merged.course_id,
        class_name=merged.class_name,
    )
    return MaterialOut.model_validate(material)
