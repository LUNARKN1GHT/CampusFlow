"""资料导入 HTTP 路由（D004 起）。"""

from dataclasses import replace
from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator

from campusflow.api.deps import get_current_workspace, get_job_queue, get_repositories
from campusflow.application import materials as use_cases
from campusflow.application.ports.parsers import DocumentParser
from campusflow.application.ports.queue import JobQueue
from campusflow.application.ports.repositories import Repositories, WorkspaceData
from campusflow.application.ports.storage import FileStorage
from campusflow.application.ports.web_fetch import WebFetcher
from campusflow.application.web_materials import import_requested_webpage
from campusflow.domain.states import MaterialSourceType, MaterialStatus
from campusflow.infrastructure.parsers.text import TextDocumentParser
from campusflow.infrastructure.web_fetch import PublicWebFetcher

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


JobQueueDep = Annotated[JobQueue | None, Depends(get_job_queue)]


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

    @field_validator("published_at", mode="before")
    @classmethod
    def reject_date_only(cls, value):
        if isinstance(value, str) and len(value.strip()) == 10:
            raise ValueError("发布时间缺少时刻，请将原始日期保留在正文，不补造零点")
        return value


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
    page: int | None
    paragraph: int | None
    bbox: tuple[float, float, float, float] | None
    confidence: float | None
    table_group: str | None


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

    @field_validator("published_at", mode="before")
    @classmethod
    def reject_date_only(cls, value):
        return TextImportIn.reject_date_only(value)


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


class DeleteImpactOut(BaseModel):
    """删除影响预览（D008/D009）：执行删除前必须持有其中 confirm_token。"""

    material: MaterialOut
    version_count: int
    chunk_count: int
    linked_task_ids: list[int]
    # 还在处理中、会被这次删除一并清掉的作业数（D009）：不为 0 时界面应提示
    unfinished_job_count: int
    confirm_token: str
    backup_note: str


class DeleteConfirmIn(BaseModel):
    confirm_token: str = Field(min_length=1)
    keep_tasks: bool = True


@router.get("/{material_id}/delete-impact", response_model=DeleteImpactOut)
def delete_impact(
    material_id: int, workspace: CurrentWorkspace, repos: Repos, request: Request
) -> DeleteImpactOut:
    impact = use_cases.preview_delete_impact(repos, workspace.id, material_id)
    days = request.app.state.settings.backup_retention_days
    return DeleteImpactOut(
        material=MaterialOut.model_validate(impact.material),
        version_count=impact.version_count,
        chunk_count=impact.chunk_count,
        linked_task_ids=impact.linked_task_ids,
        unfinished_job_count=impact.unfinished_job_count,
        confirm_token=impact.confirm_token,
        backup_note=(
            f"本地存储将立即删除；若启用备份，残留副本将在 {days} 天保留期限到期后"
            f"随备份轮换清除，且恢复流程不会复活已删除资料（见 docs/data-retention.md）。"
        ),
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


class MaterialVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    material_id: int
    version_no: int
    note: str | None
    created_at: datetime
    source_metadata: dict[str, str | None]


@router.get("/{material_id}/versions", response_model=list[MaterialVersionOut])
def list_versions(
    material_id: int, workspace: CurrentWorkspace, repos: Repos
) -> list[MaterialVersionOut]:
    return [
        MaterialVersionOut.model_validate(v)
        for v in use_cases.list_material_versions(repos, workspace.id, material_id)
    ]


@router.get("/{material_id}/versions/{version_no}/chunks", response_model=list[SourceChunkOut])
def list_chunks(
    material_id: int, version_no: int, workspace: CurrentWorkspace, repos: Repos
) -> list[SourceChunkOut]:
    return [
        SourceChunkOut.model_validate(c)
        for c in use_cases.list_version_chunks(repos, workspace.id, material_id, version_no)
    ]


@router.get("/{material_id}/versions/{version_no}/file")
def read_original_file(
    material_id: int,
    version_no: int,
    workspace: CurrentWorkspace,
    repos: Repos,
    storage: Storage,
) -> Response:
    content, storage_key = use_cases.read_original_file(
        repos, storage, workspace.id, material_id, version_no
    )
    extension = storage_key.rsplit(".", 1)[-1]
    media_types = {
        "txt": "text/plain; charset=utf-8",
        "pdf": "application/pdf",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    headers = {"X-Content-Type-Options": "nosniff"}
    if extension == "html":
        # 原网页是不可信内容，不能在应用同源下作为可执行 HTML 打开。
        headers.update(
            {
                "Content-Disposition": 'attachment; filename="snapshot.html"',
                "Content-Security-Policy": "sandbox; default-src 'none'",
            }
        )
    return Response(
        content=content,
        media_type=media_types.get(extension, "application/octet-stream"),
        headers=headers,
    )


def get_web_fetcher() -> WebFetcher:
    return PublicWebFetcher()


class WebImportIn(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=300)
    publisher: str | None = Field(default=None, max_length=200)
    published_at: datetime | None = None
    published_text: str | None = Field(default=None, max_length=200)
    semester_id: int | None = None
    course_id: int | None = None
    class_name: str | None = Field(default=None, max_length=100)

    @field_validator("published_at")
    @classmethod
    def require_offset(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("发布时间须包含时区；仅有日期时请填写 published_text")
        return value


class WebImportOut(BaseModel):
    material: MaterialOut
    version: MaterialVersionOut
    job_id: int


@router.post("/web", response_model=WebImportOut, status_code=201)
def import_web(
    payload: WebImportIn,
    workspace: CurrentWorkspace,
    repos: Repos,
    storage: Storage,
    queue: JobQueueDep,
    fetcher: Annotated[WebFetcher, Depends(get_web_fetcher)],
) -> WebImportOut:
    material, version, job_id = import_requested_webpage(
        repos,
        storage,
        fetcher,
        workspace.id,
        requested_url=payload.url,
        title=payload.title,
        publisher=payload.publisher,
        published_at=payload.published_at,
        published_text=payload.published_text,
        semester_id=payload.semester_id,
        course_id=payload.course_id,
        class_name=payload.class_name,
        queue=queue,
    )
    return WebImportOut(
        material=MaterialOut.model_validate(material),
        version=MaterialVersionOut.model_validate(version),
        job_id=job_id,
    )


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
    job_id: int | None = None


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
    queue: JobQueueDep = None,
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
        queue=queue,
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
                job_id=r.job_id,
            )
            for r in results
        ]
    )
