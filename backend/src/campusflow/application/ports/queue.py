"""作业队列端口（J002）。

消息只携带 job_id；业务状态以数据库为准，队列不作为事实来源。
"""

from typing import Protocol


class JobQueue(Protocol):
    def enqueue_parse(self, job_id: int) -> str:
        """投递解析作业，返回队列侧作业标识。

        实现应使用**稳定作业键**（J004）：同一个数据库作业重复投递时复用同一
        队列标识，便于排查，也避免补偿扫描产生语义不同的重复消息。
        """
        ...
