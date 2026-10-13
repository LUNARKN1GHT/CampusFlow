"""作业投递用例（J003）。

提交与投递分离：导入在同一事务内写入资料、版本与"待投递"作业记录并提交，
提交成功后才尝试投递队列。这样两件事同时成立：

- 队列（Redis）不可用时，资料与待投递记录都已持久化，不会丢失；
- 事务回滚（未提交）的资料不会有投递，Worker 不可能处理到未提交数据。

投递失败不抛异常：记录保持待投递，由补偿扫描（J004）重投，不影响建档结果。
"""

from dataclasses import dataclass

from campusflow.application.ports.queue import JobQueue
from campusflow.application.ports.repositories import Repositories

DEFAULT_DISPATCH_LIMIT = 100


@dataclass(frozen=True)
class DispatchResult:
    """一次投递尝试的结果统计。"""

    delivered: int
    failed: int


def dispatch_job(repos: Repositories, queue: JobQueue, job_id: int) -> bool:
    """投递单个作业。

    成功登记 enqueued_at；队列不可用时只累加尝试次数并保留待投递记录，返回 False。
    """
    try:
        queue.enqueue_parse(job_id)
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
    for job_id in targets:
        if dispatch_job(repos, queue, job_id):
            delivered += 1
        else:
            failed += 1
    return DispatchResult(delivered=delivered, failed=failed)
