"""处理作业的运维用例（J005/Q004）：查询、超时回收、重投与恢复扫描。

超时回收：Worker 进程崩溃、被强杀或卡死时，作业会一直停在"处理中"，
前端就永远等不到终态。这里把超过阈值的处理中作业标记为可重试失败，
让它回到可被重新投递的状态，而不是无限期停留。索引作业按心跳判断超时，
且不改写资料解析状态；自动重投只针对有额度剩余的索引作业。
"""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass

from campusflow.application.dispatch import dispatch_job, dispatch_pending_jobs
from campusflow.application.ports.queue import JobQueue
from campusflow.application.ports.repositories import (
    ProcessingJobAttemptData,
    ProcessingJobData,
    Repositories,
)
from campusflow.application.scope import require_in_workspace
from campusflow.domain.errors import NotFoundError
from campusflow.domain.jobs import (
    DEFAULT_EMBED_JOB_TIMEOUT_SECONDS,
    DEFAULT_EMBED_MAX_AUTO_RETRIES,
    DEFAULT_JOB_TIMEOUT_SECONDS,
    timeout_reason,
)
from campusflow.domain.states import JobStage, JobStatus, MaterialStatus

logger = logging.getLogger(__name__)


def get_job(repos: Repositories, workspace_id: int, job_id: int) -> ProcessingJobData:
    """读取当前空间内的作业（J006）。

    归属不符与不存在同样按"作业不存在"处理：不泄露其他空间作业的状态。
    """
    job = repos.jobs.get(job_id)
    if job is None:
        raise NotFoundError("作业不存在")
    require_in_workspace(job.workspace_id, workspace_id, "作业不存在")
    return job


def list_job_attempts(
    repos: Repositories, workspace_id: int, job_id: int
) -> list[ProcessingJobAttemptData]:
    """作业尝试历史（J005/J006）：先校验归属，再读历史。"""
    get_job(repos, workspace_id, job_id)
    return repos.jobs.list_attempts(job_id)


def list_material_jobs(
    repos: Repositories, workspace_id: int, material_id: int
) -> list[ProcessingJobData]:
    """资料的处理作业列表：先校验空间归属，再按版本/创建时间倒序返回。"""
    material = repos.materials.get(material_id)
    if material is None:
        raise NotFoundError("资料不存在")
    require_in_workspace(material.workspace_id, workspace_id, "资料不存在")
    return sorted(
        (
            job
            for job in repos.jobs.list_by_material(material_id)
            if job.workspace_id == workspace_id
        ),
        key=lambda job: (job.version_id, job.created_at, job.id),
        reverse=True,
    )


def reclaim_stale_jobs(
    repos: Repositories,
    *,
    timeout_seconds: int = DEFAULT_JOB_TIMEOUT_SECONDS,
    embed_timeout_seconds: int | None = None,
) -> list[ProcessingJobData]:
    """回收超时作业（J005/Q004），返回被回收的作业列表。

    只处理"领取后超过阈值仍为处理中"的作业；每个被回收的作业写入一条失败
    尝试历史（可重试）。索引作业按心跳判断超时，且**不改资料状态**：资料状态
    只代表解析结果，索引超时不能让一份解析成功的资料显示为失败。
    """
    embed_seconds = timeout_seconds if embed_timeout_seconds is None else embed_timeout_seconds
    parse_reason = timeout_reason(timeout_seconds)
    embed_reason = timeout_reason(embed_seconds)
    reclaimed: list[ProcessingJobData] = []
    for job in repos.jobs.list_stale_running(
        timeout_seconds=timeout_seconds, embed_timeout_seconds=embed_timeout_seconds
    ):
        updated = repos.jobs.record_attempt(
            job.id,
            status=JobStatus.FAILED,
            scope=None,
            error_reason=embed_reason if job.stage == JobStage.EMBED else parse_reason,
            retryable=True,
        )
        if job.stage == JobStage.PARSE:
            repos.materials.set_status(job.material_id, MaterialStatus.FAILED)
        # 作业可能已被资料删除连带清掉，此时没有可回收的记录，跳过即可。
        if updated is not None:
            reclaimed.append(updated)
    repos.uow.commit()
    return reclaimed


def retry_job(repos: Repositories, queue: JobQueue | None, job_id: int) -> ProcessingJobData | None:
    """重新处理一个可重试的作业（J005），返回重置后的作业。

    永久失败、已完成或无权重投的作业返回 None。重置与投递分开：先把待处理
    状态落库，再尝试投递；投递失败也不会回滚，记录留在待投递队列等补偿。
    索引作业重投不清资料状态：资料早已解析成功，索引只是附加的可检索性。
    """
    job = repos.jobs.prepare_retry(job_id)
    if job is None:
        return None
    if job.stage == JobStage.PARSE:
        repos.materials.set_status(job.material_id, MaterialStatus.PENDING)
    repos.uow.commit()
    dispatch_job(repos, queue, job_id)
    return repos.jobs.get(job_id)


def retry_auto_jobs(repos: Repositories, queue: JobQueue | None, *, max_auto_retries: int) -> int:
    """自动重投索引作业（Q004），返回重投数量。

    `prepare_auto_retry` 用条件更新原子地"占用"一次自动重投额度，
    并发的多个扫描进程不会把同一作业重投两次；额度用尽后只保留人工重试入口。
    """
    count = 0
    for job in repos.jobs.list_auto_retryable(
        stage=JobStage.EMBED, max_auto_retries=max_auto_retries
    ):
        if repos.jobs.prepare_auto_retry(job.id, max_auto_retries=max_auto_retries) is None:
            continue
        repos.uow.commit()
        dispatch_job(repos, queue, job.id)
        count += 1
    return count


@dataclass(frozen=True)
class RecoveryResult:
    """一次恢复扫描的结果统计（Q004）。"""

    delivered: int = 0
    reclaimed: int = 0
    auto_retried: int = 0


def run_recovery_sweep(
    open_repositories: Callable[[], AbstractContextManager[Repositories]],
    queue: JobQueue | None,
    *,
    dispatch_enabled: bool = True,
    timeout_seconds: int = DEFAULT_JOB_TIMEOUT_SECONDS,
    embed_timeout_seconds: int = DEFAULT_EMBED_JOB_TIMEOUT_SECONDS,
    max_auto_retries: int = DEFAULT_EMBED_MAX_AUTO_RETRIES,
) -> RecoveryResult:
    """一次完整恢复扫描（Q004）：补偿投递 → 超时回收 → 索引自动重投。

    三步各自使用独立的数据库会话，任何一步失败只记录日志，不影响其余两步，
    也不会让定时任务本身退出。`open_repositories` 由装配层提供（每次调用
    新建一个会话），应用层不依赖具体数据库实现。
    """
    delivered = reclaimed = auto_retried = 0

    if dispatch_enabled:
        try:
            with open_repositories() as repos:
                delivered = dispatch_pending_jobs(repos, queue).delivered
        except Exception:  # 补偿失败不影响后续恢复步骤
            logger.warning("recovery_dispatch_failed: 投递补偿失败，保留记录等待下次扫描")

    try:
        with open_repositories() as repos:
            reclaimed = len(
                reclaim_stale_jobs(
                    repos,
                    timeout_seconds=timeout_seconds,
                    embed_timeout_seconds=embed_timeout_seconds,
                )
            )
    except Exception:  # 回收失败不能让恢复循环停摆
        logger.warning("recovery_reclaim_failed: 超时回收失败，作业保留处理中状态")

    if max_auto_retries > 0:
        try:
            with open_repositories() as repos:
                auto_retried = retry_auto_jobs(repos, queue, max_auto_retries=max_auto_retries)
        except Exception:
            logger.warning("recovery_auto_retry_failed: 索引自动重投失败")

    return RecoveryResult(delivered=delivered, reclaimed=reclaimed, auto_retried=auto_retried)
