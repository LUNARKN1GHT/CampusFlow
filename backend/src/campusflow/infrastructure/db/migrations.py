"""Alembic 配置装配：脚本位置显式解析，不依赖从 backend/ 目录启动。

仓库内仍可直接执行 `alembic upgrade head`；桌面或打包运行时通过本入口
取得绝对 script_location，并复用同目录的 alembic.ini（存在时）。
"""

from alembic.config import Config

from campusflow.core.resources import resolve_migrations_dir


def build_alembic_config(migrations_dir: str | None = None) -> Config:
    script_location = resolve_migrations_dir(migrations_dir)
    ini_path = script_location.parent / "alembic.ini"
    config = Config(str(ini_path)) if ini_path.is_file() else Config()
    config.set_main_option("script_location", str(script_location))
    return config
