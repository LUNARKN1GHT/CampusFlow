"""RQ 作业队列实现（J002）。

消息只传 job_id；Worker 领取后从数据库读取作业详情执行。
"""

from __future__ import annotations

import redis
from rq import Queue

QUEUE_NAME = "campusflow-parse"


class RQJobQueue:
    def __init__(self, redis_url: str) -> None:
        self._connection = redis.Redis.from_url(redis_url)
        self._queue = Queue(QUEUE_NAME, connection=self._connection)

    def enqueue_parse(self, job_id: int) -> str:
        from campusflow.workers.jobs import parse_version_job

        job = self._queue.enqueue(parse_version_job, job_id)
        return job.id
