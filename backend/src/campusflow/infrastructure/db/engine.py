"""数据库引擎。"""

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from campusflow.core.config import Settings


def create_db_engine(settings: Settings) -> Engine:
    """创建同步引擎；create_engine 惰性连接，不访问数据库也不会报错。"""
    return create_engine(settings.database_url, pool_pre_ping=True)
