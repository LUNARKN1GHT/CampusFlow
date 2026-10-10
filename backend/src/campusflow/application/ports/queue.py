"""作业队列端口（J002）。

消息只携带 job_id；业务状态以数据库为准，队列不作为事实来源。
"""

from typing import Protocol


class JobQueue(Protocol):
    def enqueue_parse(self, job_id: int) -> str:
        """投递解析作业，返回队列侧作业标识。"""
        ...
