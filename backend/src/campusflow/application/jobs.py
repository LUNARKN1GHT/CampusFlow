"""处理作业的运维用例（J005）。

超时回收：Worker 进程崩溃、被强杀或卡死时，作业会一直停在"处理中"，
前端就永远等不到终态。这里把超过阈值的处理中作业标记为可重试失败，
让它回到可被重新投递的状态，而不是无限期停留。
"""

from campusflow.application.dispatch import dispatch_job
from campusflow.application.ports.queue import JobQueue
from campusflow.application.ports.repositories import ProcessingJobData, Repositories
from campusflow.domain.jobs import DEFAULT_JOB_TIMEOUT_SECONDS, timeout_reason
from campusflow.domain.states import JobStatus, MaterialStatus


def reclaim_stale_jobs(
    repos: Repositories, *, timeout_seconds: int = DEFAULT_JOB_TIMEOUT_SECONDS
) -> list[ProcessingJobData]:
    """回收超时作业（J005），返回被回收的作业列表。

    只处理"领取后超过 timeout_seconds 仍为处理中"的作业；每个被回收的作业
    写入一条失败尝试历史（可重试），资料状态同步为失败。
    """
    reason = timeout_reason(timeout_seconds)
    reclaimed: list[ProcessingJobData] = []
    for job in repos.jobs.list_stale_running(timeout_seconds=timeout_seconds):
        updated = repos.jobs.record_attempt(
            job.id,
            status=JobStatus.FAILED,
            scope=None,
            error_reason=reason,
            retryable=True,
        )
        repos.materials.set_status(job.material_id, MaterialStatus.FAILED)
        if updated is not None:
            reclaimed.append(updated)
    repos.uow.commit()
    return reclaimed


def retry_job(repos: Repositories, queue: JobQueue | None, job_id: int) -> ProcessingJobData | None:
    """重新处理一个可重试的作业（J005），返回重置后的作业。

    永久失败、已完成或无权重投的作业返回 None。重置与投递分开：先把待处理
    状态落库，再尝试投递；投递失败也不会回滚，记录留在待投递队列等补偿。
    """
    job = repos.jobs.prepare_retry(job_id)
    if job is None:
        return None
    repos.materials.set_status(job.material_id, MaterialStatus.PENDING)
    repos.uow.commit()
    dispatch_job(repos, queue, job_id)
    return repos.jobs.get(job_id)
