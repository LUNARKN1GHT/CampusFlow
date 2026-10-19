"""RQ 作业队列实现（J002、Q004）。

消息只传 job_id；Worker 领取后从数据库读取作业详情执行。
解析与向量索引分属不同队列：解析慢且重，索引调用多且轻，分开后互不阻塞。
"""

from __future__ import annotations

from typing import Any

import redis
from rq import Queue

QUEUE_NAME = "campusflow-parse"
INDEX_QUEUE_NAME = "campusflow-index"


def rq_job_key(job_id: int) -> str:
    """稳定作业键（J004）：同一个数据库作业始终对应同一个队列标识。

    取值限字母、数字、下划线与短横线（RQ 对自定义 job_id 的限制）。
    """
    return f"parse-{job_id}"


def rq_index_job_key(job_id: int) -> str:
    """索引作业的稳定作业键（Q004）。"""
    return f"index-{job_id}"


class RQJobQueue:
    """基于 RQ/Redis 的投递实现；消息只含 job_id，真实状态以数据库为准。"""

    def __init__(
        self, redis_url: str, *, connection: Any | None = None, connect_timeout: int = 3
    ) -> None:
        # 连接超时设短一些：Redis 不可用时投递要快速失败，而不是拖住请求或启动补偿
        # 扫描。注入 connection 仅用于测试（fakeredis），生产路径按 redis_url 建连。
        self._connection = (
            connection
            if connection is not None
            else redis.Redis.from_url(redis_url, socket_connect_timeout=connect_timeout)
        )
        self._queue = Queue(QUEUE_NAME, connection=self._connection)
        self._index_queue = Queue(INDEX_QUEUE_NAME, connection=self._connection)

    def enqueue_parse(self, job_id: int) -> str:
        """投递解析作业并返回队列侧标识；同一数据库作业始终使用同一队列标识。"""

        from campusflow.workers.jobs import parse_version_job

        # 稳定作业键：补偿扫描重投同一条待投递记录时复用同一队列标识。
        # 重复消息由数据库层的幂等领取（claim）兜底，不会重复生成片段。
        job = self._queue.enqueue(parse_version_job, job_id, job_id=rq_job_key(job_id))
        return job.id

    def enqueue_index(self, job_id: int) -> str:
        """投递向量索引作业；与解析作业分属不同队列，避免互相阻塞。"""

        from campusflow.workers.jobs import index_version_job

        # 同样使用稳定作业键；重复投递由作业领取与逐块领取共同兜底。
        job = self._index_queue.enqueue(index_version_job, job_id, job_id=rq_index_job_key(job_id))
        return job.id
