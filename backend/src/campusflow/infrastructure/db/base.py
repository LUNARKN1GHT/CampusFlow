"""SQLAlchemy 声明基类。

所有 ORM 模型继承自 Base，`Base.metadata` 同时作为 Alembic 自动生成迁移的
对照元数据；模型只在基础设施层使用，不向外暴露。
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
