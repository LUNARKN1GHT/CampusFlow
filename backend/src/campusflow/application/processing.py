"""资料处理作业用例（J002 起）。

Worker 调用本用例完成解析：读取原文件 → 按来源类型选解析器 →
片段落库 → 更新作业状态与资料状态。任何一步失败记录到作业，
不产生半成品。
"""

from campusflow.application.ports.parsers import DocumentParser
from campusflow.application.ports.repositories import Repositories, SourceChunkData
from campusflow.application.ports.storage import FileStorage
from campusflow.domain.errors import DomainError
from campusflow.domain.states import JobStatus, MaterialSourceType, MaterialStatus


class ParserRegistry:
    """按来源类型选择解析器。"""

    def __init__(
        self,
        text_parser: DocumentParser,
        pdf_parser: DocumentParser,
        vision_parser: DocumentParser,
    ) -> None:
        self._parsers = {
            MaterialSourceType.TEXT: text_parser,
            MaterialSourceType.PDF: pdf_parser,
            MaterialSourceType.IMAGE: vision_parser,
        }

    def for_type(self, source_type: MaterialSourceType) -> DocumentParser:
        return self._parsers[source_type]


def _scope_summary(fragment_count: int, failures: list) -> str:
    """覆盖范围说明：只含数量与失败范围，不含原文全文（J001 隐私规则）。"""
    scope = f"完成 {fragment_count} 个片段"
    if failures:
        locations = "、".join(
            f"第 {f.locator.page} 页" if f.locator.page else f.locator.kind for f in failures
        )
        scope += f"；未识别范围：{locations}"
    return scope


def parse_version(repos: Repositories, storage: FileStorage, job_id: int) -> list[SourceChunkData]:
    """执行解析作业（Worker 入口调用）。

    返回落库片段列表；失败时作业标记 failed 并抛出，由 Worker 层记录。
    """
    job = repos.jobs.get(job_id)
    if job is None:
        raise DomainError(f"作业不存在：{job_id}")

    repos.jobs.record_attempt(job_id, status=JobStatus.RUNNING, scope=None, error_reason=None)
    repos.uow.commit()

    versions = repos.materials.list_versions(job.material_id)
    version = next((v for v in versions if v.id == job.version_id), None)
    if version is None or version.storage_key is None:
        repos.jobs.record_attempt(
            job_id, status=JobStatus.FAILED, scope=None, error_reason="来源已不可用"
        )
        repos.uow.commit()
        raise DomainError("作业来源已不可用")

    material = repos.materials.get(job.material_id)
    if material is None:
        raise DomainError("资料不存在")

    from campusflow.application.materials import fragments_to_chunks

    try:
        content = storage.read(version.storage_key)
        parser = _PARSER_REGISTRY.for_type(material.source_type)
        outcome = parser.parse(content, _mime_for(material.source_type, version.storage_key))
        chunks = repos.materials.add_chunks(fragments_to_chunks(outcome, version.id))

        if not outcome.failures:
            status = JobStatus.DONE
        elif outcome.fragments:
            status = JobStatus.PARTIAL
        else:
            status = JobStatus.FAILED
        scope = _scope_summary(len(chunks), outcome.failures)
        repos.jobs.record_attempt(job_id, status=status, scope=scope, error_reason=None)
        _set_material_status(repos, material.id, status)
        repos.uow.commit()
        return chunks
    except Exception as exc:
        repos.uow.rollback()
        repos.jobs.record_attempt(
            job_id, status=JobStatus.FAILED, scope=None, error_reason=str(exc)
        )
        repos.uow.commit()
        raise


def _mime_for(source_type: MaterialSourceType, storage_key: str) -> str:
    extension = storage_key.rsplit(".", 1)[-1]
    if source_type == MaterialSourceType.PDF:
        return "application/pdf"
    if source_type == MaterialSourceType.TEXT:
        return "text/plain"
    return "image/jpeg" if extension in ("jpg", "jpeg") else "image/png"


def _set_material_status(repos: Repositories, material_id: int, job_status: JobStatus) -> None:
    """作业状态同步到资料处理状态。"""
    mapping = {
        JobStatus.DONE: MaterialStatus.DONE,
        JobStatus.PARTIAL: MaterialStatus.PARTIAL,
        JobStatus.FAILED: MaterialStatus.FAILED,
    }
    if job_status in mapping:
        repos.materials.set_status(material_id, mapping[job_status])


_PARSER_REGISTRY: ParserRegistry | None = None


def set_parser_registry(registry: ParserRegistry) -> None:
    """由装配入口（main/worker）注入解析器注册表。"""
    global _PARSER_REGISTRY
    _PARSER_REGISTRY = registry
