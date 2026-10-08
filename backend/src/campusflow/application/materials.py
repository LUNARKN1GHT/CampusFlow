"""资料导入用例（D004 起）。

导入流程：校验 → 保存原文件到私有存储 → 建资料与版本记录 → 切分来源片段，
全部在同一事务内提交；任何一步失败整体回滚，不产生半成品资料。
"""

from datetime import datetime

from campusflow.application.ports.repositories import (
    MaterialData,
    NewSourceChunk,
    Repositories,
    SourceChunkData,
)
from campusflow.application.ports.storage import FileStorage
from campusflow.application.scope import require_in_workspace
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.materials import (
    next_version_no,
    split_paragraphs,
    validate_pasted_text,
)
from campusflow.domain.states import MaterialSourceType, MaterialStatus


def _require_semester_in_workspace(
    repos: Repositories, workspace_id: int, semester_id: int
) -> None:
    semester = repos.semesters.get(semester_id)
    if semester is None:
        raise NotFoundError("学期不存在")
    require_in_workspace(semester.workspace_id, workspace_id, "学期不存在")


def _require_course_in_workspace(repos: Repositories, workspace_id: int, course_id: int) -> None:
    course = repos.courses.get(course_id)
    if course is None:
        raise NotFoundError("课程不存在")
    require_in_workspace(course.workspace_id, workspace_id, "课程不存在")


def _require_associations(
    repos: Repositories,
    workspace_id: int,
    semester_id: int | None,
    course_id: int | None,
) -> None:
    """校验关联归属与一致性。

    学期/课程都必须属于当前空间；两者同时提供时，课程必须属于该学期——
    不允许出现"课程在 A 学期、资料标为 B 学期"的矛盾适用范围（README §4.2）。
    """
    if semester_id is not None:
        _require_semester_in_workspace(repos, workspace_id, semester_id)
    if course_id is not None:
        _require_course_in_workspace(repos, workspace_id, course_id)
        if semester_id is not None:
            course = repos.courses.get(course_id)
            if course is not None and course.semester_id != semester_id:
                raise DomainError("课程不属于指定学期，请检查适用范围")


def import_pasted_text(
    repos: Repositories,
    storage: FileStorage,
    workspace_id: int,
    *,
    title: str,
    content: str,
    publisher: str | None,
    published_at: datetime | None,
    semester_id: int | None,
    course_id: int | None,
    class_name: str | None,
) -> tuple[MaterialData, list[SourceChunkData]]:
    """粘贴文本导入：保存为稳定原文版本并关联来源与课程范围（D004）。

    返回（资料, 段落片段列表）。片段的段落号由 split_paragraphs 固定规则生成，
    重复读取保持稳定。
    """
    error = validate_pasted_text(content)
    if error:
        raise DomainError(error)
    _require_associations(repos, workspace_id, semester_id, course_id)

    storage_key = storage.save(content.encode("utf-8"), "txt")
    try:
        material = repos.materials.create(
            workspace_id,
            semester_id=semester_id,
            course_id=course_id,
            class_name=class_name,
            title=title,
            publisher=publisher,
            published_at=published_at,
            source_type=MaterialSourceType.TEXT,
            source_url=None,
        )
        version = repos.materials.create_version(
            material.id,
            next_version_no(repos.materials.list_version_numbers(material.id)),
            "粘贴文本导入",
            storage_key,
        )
        chunks = repos.materials.add_chunks(
            [
                NewSourceChunk(
                    version_id=version.id,
                    seq=index,
                    locator_type="paragraph",
                    locator_value=str(index + 1),
                    text=paragraph,
                )
                for index, paragraph in enumerate(split_paragraphs(content))
            ]
        )
        # commit 也在受保护范围内：若提交失败，回滚数据库并删除已保存的原文件，
        # 不留下无法被资料版本引用的孤立文件。
        repos.uow.commit()
    except Exception:
        repos.uow.rollback()
        storage.delete(storage_key)
        raise
    return material, chunks


def list_materials(
    repos: Repositories,
    workspace_id: int,
    *,
    semester_id: int | None = None,
    course_id: int | None = None,
    source_type: MaterialSourceType | None = None,
    status: MaterialStatus | None = None,
    include_archived: bool = False,
) -> list[MaterialData]:
    """资料列表筛选（D005）：检索范围由学期/课程字段在查询时过滤，
    因此修订范围元数据后，后续筛选与检索自动按新范围生效。"""
    return repos.materials.list(
        workspace_id,
        semester_id=semester_id,
        course_id=course_id,
        source_type=source_type,
        status=status,
        include_archived=include_archived,
    )


def get_material(repos: Repositories, workspace_id: int, material_id: int) -> MaterialData:
    material = repos.materials.get(material_id)
    if material is None:
        raise NotFoundError("资料不存在")
    require_in_workspace(material.workspace_id, workspace_id, "资料不存在")
    return material


def update_material_metadata(
    repos: Repositories,
    workspace_id: int,
    material_id: int,
    *,
    title: str,
    publisher: str | None,
    published_at: datetime | None,
    source_url: str | None,
    semester_id: int | None,
    course_id: int | None,
    class_name: str | None,
) -> MaterialData:
    """修订资料元数据（D005）。只改元数据，不触碰任何原文版本与片段内容。"""
    current = repos.materials.get(material_id)
    if current is None:
        raise NotFoundError("资料不存在")
    require_in_workspace(current.workspace_id, workspace_id, "资料不存在")
    _require_associations(repos, workspace_id, semester_id, course_id)
    updated = repos.materials.update_metadata(
        material_id,
        title=title,
        publisher=publisher,
        published_at=published_at,
        source_url=source_url,
        semester_id=semester_id,
        course_id=course_id,
        class_name=class_name,
    )
    repos.uow.commit()
    return updated
