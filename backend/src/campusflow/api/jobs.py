"""作业状态查询与失败重试 HTTP 路由（J006）。

前端通过轮询本路由直到作业进入终态（done/partial/failed）。
按 ID 查询与重试都先校验作业属于当前空间：归属不符时与"作业不存在"
返回相同的 404，不泄露其他空间作业的状态（W006 口径）。
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from campusflow.api.deps import get_current_workspace, get_job_queue, get_repositories
from campusflow.application import jobs as use_cases
from campusflow.application.dispatch import dispatch_pending_jobs
from campusflow.application.ports.queue import JobQueue
from campusflow.application.ports.repositories import (
    ProcessingJobData,
    Repositories,
    WorkspaceData,
)
from campusflow.domain.errors import DomainError
from campusflow.domain.jobs import can_retry, is_terminal
from campusflow.domain.states import JobStage, JobStatus

router = APIRouter(prefix="/jobs", tags=["jobs"])

CurrentWorkspace = Annotated[WorkspaceData, Depends(get_current_workspace)]
Repos = Annotated[Repositories, Depends(get_repositories)]
JobQueueDep = Annotated[JobQueue | None, Depends(get_job_queue)]


class JobAttemptOut(BaseModel):
    """一次尝试的留痕（J005）：只含阶段、覆盖范围与原因，不含原文内容。"""

    model_config = ConfigDict(from_attributes=True)

    attempt_no: int
    stage: JobStage
    status: JobStatus
    scope: str | None
    error_reason: str | None
    retryable: bool
    created_at: datetime


class JobOut(BaseModel):
    """作业状态（J006 交付物）：阶段、覆盖范围、可重试性。

    is_terminal 供前端停止轮询；retryable 是"现在能否重新处理"的唯一判据
    （已完成、处理中与永久失败都为 false）；pending 且 enqueued_at 为空表示
    尚未成功投递，等补偿扫描重投。
    """

    id: int
    material_id: int
    version_id: int
    stage: JobStage
    status: JobStatus
    is_terminal: bool
    retryable: bool
    scope: str | None
    error_reason: str | None
    attempts: int
    enqueued_at: datetime | None
    started_at: datetime | None
    created_at: datetime
    updated_at: datetime


def _to_out(job: ProcessingJobData) -> JobOut:
    return JobOut(
        id=job.id,
        material_id=job.material_id,
        version_id=job.version_id,
        stage=job.stage,
        status=job.status,
        is_terminal=is_terminal(job.status),
        retryable=can_retry(job.status, job.retryable),
        scope=job.scope,
        error_reason=job.error_reason,
        attempts=job.attempts,
        enqueued_at=job.enqueued_at,
        started_at=job.started_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: int, workspace: CurrentWorkspace, repos: Repos) -> JobOut:
    return _to_out(use_cases.get_job(repos, workspace.id, job_id))


@router.get("/{job_id}/attempts", response_model=list[JobAttemptOut])
def list_job_attempts(
    job_id: int, workspace: CurrentWorkspace, repos: Repos
) -> list[JobAttemptOut]:
    return [
        JobAttemptOut.model_validate(attempt, from_attributes=True)
        for attempt in use_cases.list_job_attempts(repos, workspace.id, job_id)
    ]


@router.post("/{job_id}/retry", response_model=JobOut)
def retry_job(
    job_id: int, workspace: CurrentWorkspace, repos: Repos, queue: JobQueueDep = None
) -> JobOut:
    """重新处理失败或部分成功的作业（J005/J006）。

    只接受可重试的作业：已完成、处理中与永久失败返回 400，并说明原因，
    避免前端反复触发无效重投。
    """
    use_cases.get_job(repos, workspace.id, job_id)  # 归属校验：不匹配即 404
    job = use_cases.retry_job(repos, queue, job_id)
    if job is None:
        raise DomainError("该作业当前不可重试：已完成的作业无需重试，永久失败重试不会成功")
    return _to_out(job)


class RedeliverOut(BaseModel):
    """待投递重投结果（J004 手动入口）：队列不可用时两个计数都为 0。"""

    delivered: int
    failed: int


@router.post("/redeliver", response_model=RedeliverOut)
def redeliver_pending_jobs(
    workspace: CurrentWorkspace, repos: Repos, queue: JobQueueDep = None
) -> RedeliverOut:
    """手动重投所有待投递作业（队列曾不可用时的补救入口）。

    启动补偿（CAMPUSFLOW_DISPATCH_ON_STARTUP）被关闭时，这里是唯一的重投入口。
    MVP 只有一个默认空间，待投递记录都属于当前空间，无需再按空间过滤。
    """
    outcome = dispatch_pending_jobs(repos, queue)
    return RedeliverOut(delivered=outcome.delivered, failed=outcome.failed)
