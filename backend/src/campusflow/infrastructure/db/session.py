"""同步会话工厂。业务路由使用 def 同步函数，不引入异步 SQLAlchemy。"""

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """会话生命周期由调用方管理，仓储只 flush，提交与回滚走工作单元。

    autoflush 关闭，避免查询前触发隐式写入打乱“先校验再落库”的顺序；
    expire_on_commit 关闭，提交后仍可读取已加载对象，装配层无需重查。
    """
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
