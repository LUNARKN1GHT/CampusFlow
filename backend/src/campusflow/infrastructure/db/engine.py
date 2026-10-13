"""数据库引擎。"""

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from campusflow.core.config import DB_CONNECT_TIMEOUT_SECONDS, Settings


def create_db_engine(settings: Settings) -> Engine:
    """创建同步引擎；create_engine 惰性连接，不访问数据库也不会报错。

    连接超时与就绪检查保持一致：数据库不可达时必须快速失败，否则会拖住
    启动补偿等启动期工作（J004）。
    """
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": DB_CONNECT_TIMEOUT_SECONDS},
    )
