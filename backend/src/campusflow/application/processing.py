"""资料处理作业用例（J002 起）。

Worker 调用本用例完成解析：读取原文件 → 按来源类型选解析器 →
片段落库 → 更新作业状态与资料状态。任何一步失败记录到作业，
不产生半成品。

写入结果前必须重新核对目标（J007）：解析可能耗时较久，期间资料可能已被
删除、版本可能已被新版本取代。两种情况下都不得写回，否则会复活已删除的
资料，或让新版本收到旧版本的结果。
"""

import logging

from campusflow.application.ports.parsers import DocumentParser, ParseFailure
from campusflow.application.ports.repositories import (
    ProcessingJobData,
    Repositories,
    SourceChunkData,
)
from campusflow.application.ports.storage import FileStorage
from campusflow.domain.errors import DomainError, PermanentError
from campusflow.domain.jobs import is_retryable
from campusflow.domain.materials import (
    DELETED_MATERIAL_REASON,
    SUPERSEDED_VERSION_REASON,
    is_superseded,
)
from campusflow.domain.states import JobStatus, MaterialSourceType, MaterialStatus

logger = logging.getLogger(__name__)


def blocked_write_reason(repos: Repositories, job: ProcessingJobData) -> str | None:
    """结果写入前的目标核对（J007），可写入返回 None，否则返回阻止原因。

    读的是数据库里最新已提交的状态，而不是本事务早先加载的对象：解析可能
    耗时较久，期间资料可能已被删除（连同版本与作业一起删掉），也可能已经
    上传了更新的版本。索引与候选结果阶段同样应在写入前调用本函数。
    """
    if not repos.materials.exists(job.material_id):
        return DELETED_MATERIAL_REASON

    version_numbers = repos.materials.version_numbers_by_id(job.material_id)
    version_no = version_numbers.get(job.version_id)
    if version_no is None:
        return DELETED_MATERIAL_REASON  # 版本随资料删除，作业已无有效目标
    if is_superseded(version_no, max(version_numbers.values())):
        return SUPERSEDED_VERSION_REASON
    return None


def _abandon_write(repos: Repositories, job_id: int, reason: str) -> None:
    """放弃本次结果写入并留痕（J007）。

    作业记为不可重试的终态失败并写明原因；资料当前状态一律不动——
    已删除的资料不能因为迟到的作业复活，旧版本的结果也不能覆盖新版本的状态。
    """
    logger.warning("作业 %s 结果不写回：%s", job_id, reason)
    repos.jobs.record_attempt(
        job_id, status=JobStatus.FAILED, scope=None, error_reason=reason, retryable=False
    )
    repos.uow.commit()


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


def _scope_summary(fragment_count: int, failures: list[ParseFailure]) -> str:
    """覆盖范围说明：只含数量与失败范围，不含原文全文（J001 隐私规则）。"""
    scope = f"完成 {fragment_count} 个片段"
    if failures:
        labels = []
        for failure in failures:
            locator = failure.locator
            location = f"第 {locator.page} 页" if locator.page else ""
            if locator.paragraph:
                location += f"第 {locator.paragraph} 段"
            if locator.bbox:
                location += f"区域 {locator.bbox}（归一化）"
            labels.append(location or "位置未提供")
        locations = "、".join(labels)
        if len(locations) > 430:
            locations = locations[:430] + f"…（共 {len(failures)} 个未识别范围）"
        scope += f"；未识别范围：{locations}"
    return scope


def parse_version(repos: Repositories, storage: FileStorage, job_id: int) -> list[SourceChunkData]:
    """执行解析作业（Worker 入口调用）。

    返回落库片段列表；失败时作业标记 failed 并抛出，由 Worker 层记录。
    """
    # 幂等领取（J004）：只有仍为 pending 的作业能被领取。重复消息、未提交或
    # 已结束的作业在这里直接返回，不重复解析、不重复生成片段（J003/J004 验收）
    if not repos.jobs.claim(job_id):
        return []
    repos.uow.commit()

    job = repos.jobs.get(job_id)
    if job is None:
        return []

    # 解析前先核对一次目标（J007）：资料已被删除、或版本已被新版本取代的作业
    # 不必再花时间与费用解析（视觉识别是付费调用），直接作废并写明原因
    blocked = blocked_write_reason(repos, job)
    if blocked is not None:
        _abandon_write(repos, job_id, blocked)
        return []

    versions = repos.materials.list_versions(job.material_id)
    version = next((v for v in versions if v.id == job.version_id), None)
    if version is None or version.storage_key is None:
        # 永久失败（J005）：来源没了，重试多少次都不会成功，不标记为可重试
        repos.jobs.record_attempt(
            job_id,
            status=JobStatus.FAILED,
            scope=None,
            error_reason="来源已不可用",
            retryable=False,
        )
        repos.materials.set_status(job.material_id, MaterialStatus.FAILED)
        repos.uow.commit()
        raise PermanentError("作业来源已不可用")

    material = repos.materials.get(job.material_id)
    if material is None:
        raise DomainError("资料不存在")
    material_id = material.id  # 回滚会让 ORM 对象过期，失败路径只用标量 id

    from campusflow.application.materials import fragments_to_chunks

    try:
        content = storage.read(version.storage_key)
        parser = _PARSER_REGISTRY.for_type(material.source_type)
        outcome = parser.parse(content, _mime_for(material.source_type, version.storage_key))
        # 写入前重新核对目标（J007）：解析可能耗时较久，期间资料可能已被删除，
        # 或已经上传了更新的版本。两种情况都不写回任何片段与状态。
        blocked = blocked_write_reason(repos, job)
        if blocked is not None:
            _abandon_write(repos, job_id, blocked)
            return []
        chunks = repos.materials.add_chunks(fragments_to_chunks(outcome, version.id))

        if not outcome.failures:
            status = JobStatus.DONE
        elif outcome.fragments:
            status = JobStatus.PARTIAL
        else:
            status = JobStatus.FAILED
        scope = _scope_summary(len(chunks), outcome.failures)
        # 全页失败时把失败原因写进作业，便于状态查询与诊断（J005）
        error_reason = (
            "；".join(dict.fromkeys(f.reason for f in outcome.failures))
            if outcome.failures
            else None
        )
        # 分阶段失败重试（J005）：只要有失败范围是临时故障，就允许重试补齐；
        # 已完成页面的片段已落库，重试不会重复写入
        repos.jobs.record_attempt(
            job_id,
            status=status,
            scope=scope,
            error_reason=error_reason,
            retryable=any(f.retryable for f in outcome.failures),
        )
        _set_material_status(repos, material_id, status)
        repos.uow.commit()
        return chunks
    except Exception as exc:
        repos.uow.rollback()
        # 临时故障（限流、超时、上游 5xx）标为可重试；其余按永久失败处理（J005）
        repos.jobs.record_attempt(
            job_id,
            status=JobStatus.FAILED,
            scope=None,
            error_reason=str(exc),
            retryable=is_retryable(exc),
        )
        repos.materials.set_status(material_id, MaterialStatus.FAILED)
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
