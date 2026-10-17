"""Q004 索引用例：准备完整清单 → 逐块领取 → 事务外调用模型 → 凭凭证保存 → 汇总。

事务边界是这里的核心约束：

- 领取块与保存结果各自用一个短事务，提交之后才向模型发起请求；
- 调用模型期间不持有任何事务、锁或数据库连接；
- 写回必须凭领取时发出的凭证，过期凭证只能作废，不能覆盖新结果。
"""

import logging

from campusflow.application.embeddings import register_embedder_profile
from campusflow.application.ports.embeddings import Embedder, EmbeddingProfiles
from campusflow.application.ports.indexing import IndexManifests
from campusflow.application.ports.repositories import Repositories
from campusflow.application.processing import abandon_write, blocked_write_reason
from campusflow.domain.errors import DomainError, NotFoundError, PermanentError, TransientError
from campusflow.domain.indexing import (
    DEFAULT_BLOCK_LEASE_SECONDS,
    MAX_BLOCK_ATTEMPTS,
    IndexProgressSummary,
)
from campusflow.domain.states import JobStage, JobStatus

logger = logging.getLogger(__name__)

_SOURCE_CHANGED_REASON = "索引来源在运行中变化，需要重新生成清单"
_SOURCE_UNAVAILABLE_REASON = "索引来源不可用或已归档"
_PROFILE_MISMATCH_REASON = "向量配置未登记或与调用模型不一致"
_STAGE_MISMATCH_REASON = "作业阶段与索引用例不匹配"


def index_version(
    repos: Repositories,
    manifests: IndexManifests,
    profiles: EmbeddingProfiles,
    embedder: Embedder,
    job_id: int,
    *,
    lease_seconds: int = DEFAULT_BLOCK_LEASE_SECONDS,
    max_attempts: int = MAX_BLOCK_ATTEMPTS,
) -> IndexProgressSummary | None:
    """执行一个向量索引作业（Worker 入口调用）。

    返回汇总；重复消息、阶段不符、来源已删除或旧版本被取代时返回 None。
    """
    # 幂等领取（J004）：重复消息与已结束作业在这里直接返回，不产生模型调用。
    if not repos.jobs.claim(job_id):
        return None
    repos.uow.commit()

    job = repos.jobs.get(job_id)
    if job is None:
        return None
    if job.stage != JobStage.EMBED:
        _record_failure(repos, job_id, _STAGE_MISMATCH_REASON, retryable=False)
        return None

    blocked = blocked_write_reason(repos, job)
    if blocked is not None:
        abandon_write(repos, job_id, blocked)
        return None

    try:
        profile = register_embedder_profile(profiles, embedder)
        repos.uow.commit()
    except DomainError:
        repos.uow.rollback()
        _record_failure(repos, job_id, _PROFILE_MISMATCH_REASON, retryable=False)
        return None

    try:
        manifest_id = manifests.prepare(job.workspace_id, job.version_id, profile.id)
        repos.jobs.set_manifest(job_id, manifest_id)
        repos.uow.commit()
    except (DomainError, NotFoundError):
        repos.uow.rollback()
        _abandon_or_fail(repos, job, retryable=False, reason=_SOURCE_UNAVAILABLE_REASON)
        return None

    try:
        _index_blocks(
            repos,
            manifests,
            embedder,
            job_id,
            job.workspace_id,
            manifest_id,
            lease_seconds=lease_seconds,
            max_attempts=max_attempts,
        )
    except (DomainError, NotFoundError):
        # 运行中来源被重新解析/删除：已成功块保留，剩余块由下一次运行补齐。
        repos.uow.rollback()
        _abandon_or_fail(repos, job, retryable=True, reason=_SOURCE_CHANGED_REASON)
        return None

    try:
        summary = manifests.summary(job.workspace_id, manifest_id, verify_snapshot=True)
    except (DomainError, NotFoundError):
        repos.uow.rollback()
        _abandon_or_fail(repos, job, retryable=True, reason=_SOURCE_CHANGED_REASON)
        return None

    if summary.pending or summary.running or summary.retryable_failed:
        # 仍有块被其他执行者持有或仍可重试：不宣布终态，交给超时回收与自动重投。
        repos.uow.rollback()
        logger.info("索引作业 %s 尚有未完成块，保留处理中状态", job_id)
        return None

    repos.jobs.record_attempt(
        job_id,
        status=summary.status,
        scope=_scope_text(summary),
        error_reason=_error_reason(summary),
        retryable=False,
    )
    repos.uow.commit()
    return summary


def _index_blocks(
    repos: Repositories,
    manifests: IndexManifests,
    embedder: Embedder,
    job_id: int,
    workspace_id: int,
    manifest_id: str,
    *,
    lease_seconds: int,
    max_attempts: int,
) -> None:
    """逐块处理：领取与保存各一个短事务，模型调用在事务之外。"""
    while True:
        claim = manifests.claim_next(
            workspace_id,
            manifest_id,
            lease_seconds=lease_seconds,
            max_attempts=max_attempts,
            verify_snapshot=False,
        )
        if claim is None:
            repos.uow.rollback()
            return
        repos.jobs.heartbeat(job_id)
        repos.uow.commit()

        try:
            result = embedder.embed(claim.text)
            result.validate_for(embedder.profile)
        except Exception as exc:  # noqa: BLE001 - 统一映射为安全失败码
            code = _failure_code(exc)
            manifests.save_failure(
                workspace_id,
                manifest_id,
                claim,
                code,
                max_attempts=max_attempts,
                verify_snapshot=False,
            )
            repos.uow.commit()
            continue

        saved = manifests.save_success(
            workspace_id, manifest_id, claim, result, verify_snapshot=False
        )
        repos.uow.commit()
        if not saved:
            # 凭证已被新的领取替换：本次结果作废，继续处理下一个块。
            continue


def _failure_code(exc: BaseException) -> str:
    """把异常映射为可安全写入数据库的失败码。"""
    if isinstance(exc, NotFoundError):
        return "source_unavailable"
    if isinstance(exc, TransientError):
        return "upstream_transient"
    if isinstance(exc, (PermanentError, DomainError)):
        return "invalid_response"
    # 未预期异常按临时失败处理：仍受每块次数上限约束，不会无限重试。
    logger.warning("索引块出现未预期异常：%s", type(exc).__name__)
    return "upstream_transient"


def _abandon_or_fail(repos: Repositories, job, *, retryable: bool, reason: str) -> None:
    """来源已失效时作废；否则记为可重试失败，交给恢复扫描重建清单。"""
    blocked = blocked_write_reason(repos, job)
    if blocked is not None:
        abandon_write(repos, job.id, blocked)
        return
    _record_failure(repos, job.id, reason, retryable=retryable)


def _record_failure(repos: Repositories, job_id: int, reason: str, *, retryable: bool) -> None:
    repos.jobs.record_attempt(
        job_id,
        status=JobStatus.FAILED,
        scope=None,
        error_reason=reason,
        retryable=retryable,
    )
    repos.uow.commit()


def _scope_text(summary: IndexProgressSummary) -> str:
    """只写数量与覆盖情况，不含原文、异常或密钥（J001 隐私规则）。"""
    text = (
        f"预期 {summary.expected} 块；完成 {summary.indexed}；"
        f"失败 {summary.failed}；跳过 {summary.skipped}"
    )
    if not summary.source_coverage_known:
        text += "；解析覆盖未知"
    if summary.uncovered_source_count:
        text += f"；未覆盖 {summary.uncovered_source_count} 处"
    return text[:500]


def _error_reason(summary: IndexProgressSummary) -> str | None:
    if summary.status == JobStatus.FAILED:
        return "索引全部块失败"
    if summary.status == JobStatus.PARTIAL:
        return "部分索引块失败，结果不完整"
    return None
