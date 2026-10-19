"""作业投递用例（J003）。

提交与投递分离：导入在同一事务内写入资料、版本与"待投递"作业记录并提交，
提交成功后才尝试投递队列。这样两件事同时成立：

- 队列（Redis）不可用时，资料与待投递记录都已持久化，不会丢失；
- 事务回滚（未提交）的资料不会有投递，Worker 不可能处理到未提交数据。

投递失败不抛异常：记录保持待投递，由补偿扫描（J004）重投，不影响建档结果。
投递只负责入队，作业执行与结果记录由 Worker 负责。
"""

from dataclasses import dataclass

from campusflow.application.ports.queue import JobQueue
from campusflow.application.ports.repositories import Repositories
from campusflow.domain.states import JobStage, JobStatus

DEFAULT_DISPATCH_LIMIT = 100


@dataclass(frozen=True)
class DispatchResult:
    """一次投递尝试的结果统计。"""

    delivered: int
    failed: int


def dispatch_job(repos: Repositories, queue: JobQueue, job_id: int) -> bool:
    """按阶段投递单个作业（Q004）。

    成功登记 enqueued_at；队列不可用时只累加尝试次数并保留待投递记录，返回 False。
    没有对应 Worker 入口的阶段不投递，直接记为不可重试失败。
    """
    job = repos.jobs.get(job_id)
    if job is None:
        # 作业已随资料删除：不必投递，也不必登记。
        return False
    if job.stage == JobStage.PARSE:
        enqueue = queue.enqueue_parse
    elif job.stage == JobStage.EMBED:
        enqueue = queue.enqueue_index
    else:
        # 目前只有解析与索引阶段有 Worker 入口；其余阶段没有可执行处理器，标记为不可重试。
        repos.jobs.record_attempt(
            job_id,
            status=JobStatus.FAILED,
            scope=None,
            error_reason="作业阶段没有对应的处理入口",
            retryable=False,
        )
        repos.uow.commit()
        return False
    try:
        enqueue(job_id)
    except Exception:
        # 队列侧故障（连接失败、超时等）不属于业务失败：不抛出、不回滚已提交数据
        repos.jobs.record_delivery(job_id, delivered=False)
        repos.uow.commit()
        return False
    repos.jobs.record_delivery(job_id, delivered=True)
    repos.uow.commit()
    return True


def dispatch_pending_jobs(
    repos: Repositories,
    queue: JobQueue | None,
    *,
    job_ids: list[int] | None = None,
    limit: int = DEFAULT_DISPATCH_LIMIT,
    workspace_id: int | None = None,
) -> DispatchResult:
    """投递作业。

    传入 job_ids 时只投递这些作业（导入流程用）；否则从待投递记录中取一批
    （补偿扫描用，J004）。未配置队列时不做任何事，记录留待补偿。
    """
    if queue is None:
        return DispatchResult(delivered=0, failed=0)

    if job_ids is None:
        targets = [
            job.id for job in repos.jobs.list_undelivered(limit=limit, workspace_id=workspace_id)
        ]
    else:
        targets = job_ids
    delivered = failed = 0
    # 逐个作业投递并各自提交，单个失败不影响其它作业的投递结果。
    for job_id in targets:
        if dispatch_job(repos, queue, job_id):
            delivered += 1
        else:
            failed += 1
    return DispatchResult(delivered=delivered, failed=failed)
