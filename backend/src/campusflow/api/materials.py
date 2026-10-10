"""资料导入 HTTP 路由（D004 起）。"""

from dataclasses import replace
from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator

from campusflow.api.deps import get_current_workspace, get_repositories
from campusflow.application import materials as use_cases
from campusflow.application.ports.repositories import Repositories, WorkspaceData
from campusflow.application.ports.storage import FileStorage
from campusflow.domain.states import MaterialSourceType, MaterialStatus

router = APIRouter(prefix="/materials", tags=["materials"])

CurrentWorkspace = Annotated[WorkspaceData, Depends(get_current_workspace)]
Repos = Annotated[Repositories, Depends(get_repositories)]

LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def get_file_storage(request: Request) -> FileStorage:
    return request.app.state.file_storage


Storage = Annotated[FileStorage, Depends(get_file_storage)]


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
    payload: TextImportIn, workspace: CurrentWorkspace, repos: Repos, storage: Storage
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


class DeleteImpactOut(BaseModel):
    """删除影响预览（D008）：执行删除前必须持有其中 confirm_token。"""

    material: MaterialOut
    version_count: int
    chunk_count: int
    linked_task_ids: list[int]
    confirm_token: str


class DeleteConfirmIn(BaseModel):
    confirm_token: str = Field(min_length=1)
    keep_tasks: bool = True


@router.get("/{material_id}/delete-impact", response_model=DeleteImpactOut)
def delete_impact(material_id: int, workspace: CurrentWorkspace, repos: Repos) -> DeleteImpactOut:
    impact = use_cases.preview_delete_impact(repos, workspace.id, material_id)
    return DeleteImpactOut(
        material=MaterialOut.model_validate(impact.material),
        version_count=impact.version_count,
        chunk_count=impact.chunk_count,
        linked_task_ids=impact.linked_task_ids,
        confirm_token=impact.confirm_token,
    )


@router.post("/{material_id}/delete", status_code=204)
def delete_material(
    material_id: int,
    payload: DeleteConfirmIn,
    workspace: CurrentWorkspace,
    repos: Repos,
    storage: Storage,
) -> Response:
    use_cases.delete_material(
        repos,
        storage,
        workspace.id,
        material_id,
        confirm_token=payload.confirm_token,
        keep_tasks=payload.keep_tasks,
    )
    return Response(status_code=204)


@router.get("/{material_id}", response_model=MaterialOut)
def get_material(material_id: int, workspace: CurrentWorkspace, repos: Repos) -> MaterialOut:
    return MaterialOut.model_validate(use_cases.get_material(repos, workspace.id, material_id))


@router.post("/{material_id}/archive", response_model=MaterialOut)
def archive_material(material_id: int, workspace: CurrentWorkspace, repos: Repos) -> MaterialOut:
    return MaterialOut.model_validate(
        use_cases.set_material_archived(repos, workspace.id, material_id, archived=True)
    )


@router.post("/{material_id}/restore", response_model=MaterialOut)
def restore_material(material_id: int, workspace: CurrentWorkspace, repos: Repos) -> MaterialOut:
    return MaterialOut.model_validate(
        use_cases.set_material_archived(repos, workspace.id, material_id, archived=False)
    )


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


class UploadItemOut(BaseModel):
    """单个文件的上传结果：成功带资料，失败带可读原因，完全重复带已有资料提示（D003/D006）。"""

    filename: str
    material: MaterialOut | None
    error: str | None
    duplicate_of: MaterialOut | None = None


class UploadOut(BaseModel):
    results: list[UploadItemOut]


@router.post("/upload", response_model=UploadOut, status_code=201)
def upload_files(
    workspace: CurrentWorkspace,
    repos: Repos,
    storage: Storage,
    request: Request,
    files: Annotated[list[UploadFile], File()],
    semester_id: Annotated[int | None, Query()] = None,
    course_id: Annotated[int | None, Query()] = None,
    class_name: Annotated[str | None, Query()] = None,
    publisher: Annotated[str | None, Query()] = None,
    allow_duplicate: Annotated[bool, Query()] = False,
) -> UploadOut:
    settings = request.app.state.settings
    if len(files) > settings.upload_max_files:
        from campusflow.domain.errors import DomainError

        raise DomainError(f"一次最多上传 {settings.upload_max_files} 个文件")

    results = use_cases.import_uploaded_files(
        repos,
        storage,
        workspace.id,
        files=[(f.filename or "未命名", f.file.read()) for f in files],
        publisher=publisher,
        published_at=None,
        semester_id=semester_id,
        course_id=course_id,
        class_name=class_name,
        max_bytes=settings.upload_max_bytes,
        allow_duplicate=allow_duplicate,
    )
    return UploadOut(
        results=[
            UploadItemOut(
                filename=r.filename,
                material=MaterialOut.model_validate(r.material) if r.material else None,
                error=r.error,
                duplicate_of=(
                    MaterialOut.model_validate(r.duplicate_of) if r.duplicate_of else None
                ),
            )
            for r in results
        ]
    )
