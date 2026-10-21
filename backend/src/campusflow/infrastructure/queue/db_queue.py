"""桌面模式作业队列（P005）：只依赖 PostgreSQL，不连接 Redis。

入队只返回稳定的本地标识；真正的"投递"语义是作业已经提交到
processing_jobs，由数据库轮询 Worker 直接领取。`dispatch_job` 仍会登记
投递时间，避免恢复扫描把同一作业当成未投递而反复补偿。
"""

DESKTOP_PARSE_JOB_PREFIX = "desktop-parse-"
DESKTOP_INDEX_JOB_PREFIX = "desktop-index-"


class DBJobQueue:
    """数据库轮询模式的队列占位实现；不保存状态，也不触碰数据库。"""

    def enqueue_parse(self, job_id: int) -> str:
        """返回解析作业的稳定本地标识；同一数据库作业始终得到同一标识。"""
        return f"{DESKTOP_PARSE_JOB_PREFIX}{job_id}"

    def enqueue_index(self, job_id: int) -> str:
        """返回索引作业的稳定本地标识；与解析作业区分，便于排查。"""
        return f"{DESKTOP_INDEX_JOB_PREFIX}{job_id}"
