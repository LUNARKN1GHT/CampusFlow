"""作业状态查询与失败重试 HTTP 路由（J006）。

前端通过轮询本路由直到作业进入终态（done/partial/failed）。
按 ID 查询与重试都先校验作业属于当前空间：归属不符时与"作业不存在"
返回相同的 404，不泄露其他空间作业的状态（W006 口径）。
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from campusflow.api.deps import (
    get_current_workspace,
    get_index_manifests,
    get_job_queue,
    get_repositories,
)
from campusflow.application import jobs as use_cases
from campusflow.application.dispatch import dispatch_pending_jobs
from campusflow.application.ports.indexing import IndexManifests
from campusflow.application.ports.queue import JobQueue
from campusflow.application.ports.repositories import (
    ProcessingJobData,
    Repositories,
    WorkspaceData,
)
from campusflow.domain.errors import DomainError
from campusflow.domain.indexing import (
    BlockIndexProgress,
    BlockIndexState,
    IndexProgressSummary,
)
from campusflow.domain.jobs import can_retry, is_terminal
from campusflow.domain.states import JobStage, JobStatus

router = APIRouter(prefix="/jobs", tags=["jobs"])

CurrentWorkspace = Annotated[WorkspaceData, Depends(get_current_workspace)]
Repos = Annotated[Repositories, Depends(get_repositories)]
JobQueueDep = Annotated[JobQueue | None, Depends(get_job_queue)]
IndexManifestsDep = Annotated[IndexManifests, Depends(get_index_manifests)]


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


@router.get("", response_model=list[JobOut])
def list_material_jobs(
    material_id: Annotated[int, Query(gt=0)], workspace: CurrentWorkspace, repos: Repos
) -> list[JobOut]:
    """先校验资料属于当前空间；跨空间与不存在同样返回 404。"""
    return [_to_out(job) for job in use_cases.list_material_jobs(repos, workspace.id, material_id)]


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: int, workspace: CurrentWorkspace, repos: Repos) -> JobOut:
    return _to_out(use_cases.get_job(repos, workspace.id, job_id))


@router.get("/{job_id}/attempts", response_model=list[JobAttemptOut])
def list_job_attempts(
    job_id: int, workspace: CurrentWorkspace, repos: Repos
) -> list[JobAttemptOut]:
    """尝试历史只含阶段、覆盖范围与原因，不返回原文或模型输出。"""
    return [
        JobAttemptOut.model_validate(attempt, from_attributes=True)
        for attempt in use_cases.list_job_attempts(repos, workspace.id, job_id)
    ]


class IndexSummaryOut(BaseModel):
    """索引进度汇总（Q004）：只有完整覆盖且全部块成功才算完全可检索。"""

    status: JobStatus
    expected: int
    indexed: int
    pending: int
    running: int
    failed: int
    skipped: int
    retryable_failed: int
    source_coverage_known: bool
    uncovered_source_count: int
    fully_searchable: bool


class IndexBlockOut(BaseModel):
    """单块进度：只含状态与失败码，不返回块正文或向量。"""

    block_id: str
    state: BlockIndexState
    attempts: int
    retryable: bool
    failure_code: str | None


class IndexProgressOut(BaseModel):
    """逐块索引进度（Q004）。

    manifest_id 为空表示尚未开始索引（等待队列扫描投递）；此时 summary 为 null，
    前端应显示"等待索引"。blocks 仅在 include_blocks=true 时返回。
    """

    job_id: int
    manifest_id: str | None
    summary: IndexSummaryOut | None
    blocks: list[IndexBlockOut] | None


def _summary_out(summary: IndexProgressSummary) -> IndexSummaryOut:
    return IndexSummaryOut(
        status=summary.status,
        expected=summary.expected,
        indexed=summary.indexed,
        pending=summary.pending,
        running=summary.running,
        failed=summary.failed,
        skipped=summary.skipped,
        retryable_failed=summary.retryable_failed,
        source_coverage_known=summary.source_coverage_known,
        uncovered_source_count=summary.uncovered_source_count,
        fully_searchable=summary.fully_searchable,
    )


def _block_out(progress: BlockIndexProgress) -> IndexBlockOut:
    return IndexBlockOut(
        block_id=progress.block_id,
        state=progress.state,
        attempts=progress.attempts,
        retryable=progress.retryable,
        failure_code=progress.failure_code,
    )


@router.get("/{job_id}/index-progress", response_model=IndexProgressOut)
def get_index_progress(
    job_id: int,
    workspace: CurrentWorkspace,
    repos: Repos,
    manifests: IndexManifestsDep,
    include_blocks: Annotated[bool, Query()] = False,
) -> IndexProgressOut:
    """索引作业的逐块进度（Q004）。

    复用作业归属校验：越权与不存在同为 404。非索引作业不提供该视图。
    尚未生成清单时返回空汇总，而不是假装"还没有失败"。
    """
    job = use_cases.get_job(repos, workspace.id, job_id)
    if job.stage != JobStage.EMBED:
        raise DomainError("只有向量索引作业支持逐块进度查询")
    if job.manifest_id is None:
        return IndexProgressOut(
            job_id=job.id,
            manifest_id=None,
            summary=None,
            blocks=[] if include_blocks else None,
        )
    # 只读查询不重算快照：避免 GET 请求长时间锁定资料与全部片段。
    summary = manifests.summary(workspace.id, job.manifest_id, verify_snapshot=False)
    blocks = (
        [_block_out(item) for item in manifests.blocks_progress(workspace.id, job.manifest_id)]
        if include_blocks
        else None
    )
    return IndexProgressOut(
        job_id=job.id,
        manifest_id=job.manifest_id,
        summary=_summary_out(summary),
        blocks=blocks,
    )


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
    只重投当前空间的作业，不能因为同一部署是单用户界面就省略空间边界。
    """
    outcome = dispatch_pending_jobs(repos, queue, workspace_id=workspace.id)
    return RedeliverOut(delivered=outcome.delivered, failed=outcome.failed)
