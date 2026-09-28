"""SQLAlchemy 声明基类。所有 ORM 模型继承自 Base。"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
