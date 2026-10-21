"""Alembic 配置装配：脚本位置显式解析，不依赖从 backend/ 目录启动。

仓库内仍可直接执行 `alembic upgrade head`；桌面或打包运行时通过本入口
取得绝对 script_location，并复用同目录的 alembic.ini（存在时）。
"""

import os

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from campusflow.core.resources import resolve_migrations_dir


def build_alembic_config(migrations_dir: str | None = None) -> Config:
    script_location = resolve_migrations_dir(migrations_dir)
    ini_path = script_location.parent / "alembic.ini"
    config = Config(str(ini_path)) if ini_path.is_file() else Config()
    config.set_main_option("script_location", str(script_location))
    return config


def build_runtime_alembic_config(migrations_dir: str | None = None) -> Config:
    """不加载 ini 的运行时配置：避免 env.py 里的 fileConfig 关闭应用日志。"""
    config = Config()
    config.set_main_option("script_location", str(resolve_migrations_dir(migrations_dir)))
    return config


def upgrade_to_head(database_url: str, migrations_dir: str | None = None) -> str:
    """执行 `alembic upgrade head` 并返回脚本头。

    migrations/env.py 从进程环境读取 CAMPUSFLOW_DATABASE_URL，因此这里临时覆盖
    该变量并在结束后恢复。只用于服务启动前的初始化阶段，不与其它连接并发执行。
    """
    config = build_runtime_alembic_config(migrations_dir)
    previous = os.environ.get("CAMPUSFLOW_DATABASE_URL")
    os.environ["CAMPUSFLOW_DATABASE_URL"] = database_url
    try:
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("CAMPUSFLOW_DATABASE_URL", None)
        else:
            os.environ["CAMPUSFLOW_DATABASE_URL"] = previous
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise RuntimeError("迁移脚本没有 head，无法确认数据库结构")
    return head
