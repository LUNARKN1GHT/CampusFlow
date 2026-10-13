"""RQ 作业队列实现（J002）。

消息只传 job_id；Worker 领取后从数据库读取作业详情执行。
"""

from __future__ import annotations

from typing import Any

import redis
from rq import Queue

QUEUE_NAME = "campusflow-parse"


def rq_job_key(job_id: int) -> str:
    """稳定作业键（J004）：同一个数据库作业始终对应同一个队列标识。

    取值限字母、数字、下划线与短横线（RQ 对自定义 job_id 的限制）。
    """
    return f"parse-{job_id}"


class RQJobQueue:
    def __init__(
        self, redis_url: str, *, connection: Any | None = None, connect_timeout: int = 3
    ) -> None:
        # 连接超时设短一些：Redis 不可用时投递要快速失败，而不是拖住请求或启动补偿
        self._connection = (
            connection
            if connection is not None
            else redis.Redis.from_url(redis_url, socket_connect_timeout=connect_timeout)
        )
        self._queue = Queue(QUEUE_NAME, connection=self._connection)

    def enqueue_parse(self, job_id: int) -> str:
        from campusflow.workers.jobs import parse_version_job

        # 稳定作业键：补偿扫描重投同一条待投递记录时复用同一队列标识。
        # 重复消息由数据库层的幂等领取（claim）兜底，不会重复生成片段。
        job = self._queue.enqueue(parse_version_job, job_id, job_id=rq_job_key(job_id))
        return job.id
