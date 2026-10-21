"""作业队列装配（P005）：按部署模式选择 RQ 或数据库轮询。

桌面模式只依赖 PostgreSQL，不导入 redis/rq；服务端模式保持原有 RQ 行为。
rq_queue 在服务端分支内延迟导入，避免桌面进程加载无关依赖。
"""

from campusflow.application.ports.queue import JobQueue
from campusflow.core.config import Settings
from campusflow.infrastructure.queue.db_queue import DBJobQueue

SERVER_DEPLOYMENT_MODE = "server"
DESKTOP_DEPLOYMENT_MODE = "desktop"


def build_job_queue(settings: Settings) -> JobQueue:
    """按 CAMPUSFLOW_DEPLOYMENT_MODE 装配队列；未知模式直接拒绝。"""
    mode = settings.deployment_mode
    if mode == DESKTOP_DEPLOYMENT_MODE:
        return DBJobQueue()
    if mode != SERVER_DEPLOYMENT_MODE:
        raise ValueError("CAMPUSFLOW_DEPLOYMENT_MODE 只接受 server 或 desktop")
    from campusflow.infrastructure.queue.rq_queue import RQJobQueue

    return RQJobQueue(settings.redis_url)
