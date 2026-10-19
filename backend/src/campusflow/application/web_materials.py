"""N005 公开网页导入：显式请求 → 不可变原文快照 → 待处理解析作业。

联网前先拒绝不存在的空间与跨空间关联；抓取时间与发布时间分开保存，
不把抓取时间冒充发布信息。快照、版本与作业同事务落库，失败不留孤立文件。
"""

import hashlib
from datetime import UTC, datetime

from campusflow.application.dispatch import dispatch_pending_jobs
from campusflow.application.materials import _require_associations
from campusflow.application.ports.queue import JobQueue
from campusflow.application.ports.repositories import (
    MaterialData,
    MaterialVersionData,
    Repositories,
)
from campusflow.application.ports.storage import FileStorage
from campusflow.application.ports.web_fetch import WebFetcher, WebFetchError
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.domain.states import MaterialSourceType


def import_requested_webpage(
    repos: Repositories,
    storage: FileStorage,
    fetcher: WebFetcher,
    workspace_id: int,
    *,
    requested_url: str,
    title: str,
    publisher: str | None = None,
    published_at: datetime | None = None,
    published_text: str | None = None,
    semester_id: int | None = None,
    course_id: int | None = None,
    class_name: str | None = None,
    queue: JobQueue | None = None,
) -> tuple[MaterialData, MaterialVersionData, int]:
    """抓取指定公开网页并建立版本 1 的不可变快照，返回（资料, 版本, 作业号）。

    只有显式调用才联网；抓取失败不落库。原始响应作为受保护附件保存，
    解析与正文提取交给后续作业，抓取时间不冒充发布时间。
    """
    # 在联网之前拒绝不存在的空间及跨空间关联。
    if repos.workspaces.get(workspace_id) is None:
        raise NotFoundError("工作空间不存在")
    _require_associations(repos, workspace_id, semester_id, course_id)
    try:
        page = fetcher.fetch(requested_url)
    except WebFetchError as exc:
        raise DomainError(str(exc)) from None
    fetched_at = datetime.now(UTC)
    # 去掉响应头的 charset 等参数再判断类型；只有纯文本直接存 .txt，其余按 HTML 快照。
    content_type = page.content_type.split(";", 1)[0].strip().lower()
    extension = "txt" if content_type == "text/plain" else "html"
    storage_key = storage.save(page.body, extension)
    metadata = {
        "requested_url": page.requested_url,
        "final_url": page.final_url,
        "fetched_at": fetched_at.isoformat(),
        "content_type": page.content_type,
        "publisher": publisher,
        "published_at": published_at.isoformat() if published_at is not None else None,
        "published_text": published_text,
        # 只有用户主动填写了发布方、发布时间或发布说明，才声明发布信息来自用户。
        "publication_basis": "user_provided"
        if publisher or published_at or published_text
        else None,
    }
    try:
        material = repos.materials.create(
            workspace_id,
            semester_id=semester_id,
            course_id=course_id,
            class_name=class_name,
            title=title,
            publisher=publisher,
            published_at=published_at,
            source_type=MaterialSourceType.WEB,
            # 来源地址保留用户请求的 URL；跳转后的最终地址另存于版本元数据。
            source_url=requested_url,
        )
        version = repos.materials.create_version(
            material.id,
            1,
            "用户请求的公开网页快照",
            storage_key,
            hashlib.sha256(page.body).hexdigest(),
            source_metadata=metadata,
        )
        job = repos.jobs.create(workspace_id, material.id, version.id, "parse")
        repos.uow.commit()
    except Exception:
        # 回滚数据库后删除已保存的快照，不留无法被版本引用的孤立文件。
        repos.uow.rollback()
        storage.delete(storage_key)
        raise
    # 提交与投递分离（J003）：投递失败不抛异常，记录留待补偿扫描。
    dispatch_pending_jobs(repos, queue, job_ids=[job.id])
    return material, version, job.id
