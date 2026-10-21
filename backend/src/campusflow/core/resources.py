"""运行资源定位：打包或桌面模式不依赖进程工作目录与仓库相对位置。

迁移脚本可能来自环境变量显式路径、PyInstaller 解包目录或安装包内的
campusflow/migrations；源码运行时回退到 backend/migrations。
"""

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from campusflow.core.config import Settings

_PACKAGE_DIR = Path(__file__).resolve().parents[1]
_REPO_BACKEND_DIR = Path(__file__).resolve().parents[3]


def _migration_candidates(migrations_dir: str | None) -> list[Path]:
    if migrations_dir is not None:
        return [Path(migrations_dir).expanduser()]
    candidates: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root is not None:
        root = Path(bundle_root)
        candidates.extend(
            (
                root / "migrations",
                root / "campusflow" / "migrations",
                root / "backend" / "migrations",
            )
        )
    candidates.extend((_PACKAGE_DIR / "migrations", _REPO_BACKEND_DIR / "migrations"))
    return candidates


def resolve_migrations_dir(migrations_dir: str | None = None) -> Path:
    """返回迁移脚本目录；显式路径无效时直接报错，不回退到其他位置。"""
    candidates = _migration_candidates(migrations_dir)
    for candidate in candidates:
        path = candidate.resolve()
        if path.is_dir():
            return path
    if migrations_dir is not None:
        raise FileNotFoundError(f"迁移目录不存在：{migrations_dir}")
    searched = "、".join(str(candidate) for candidate in candidates)
    raise FileNotFoundError(f"未找到数据库迁移目录（已检查：{searched}）")


def pg_binary_dir(pg_bin: str | None = None) -> Path | None:
    """解析 PostgreSQL 客户端工具目录（P010）。

    `CAMPUSFLOW_PG_BIN` 可以指向目录，也可以直接指向某个可执行文件；
    未配置时返回 None，由调用方在 PATH 中查找。这里不校验存在性，
    以便错误信息同时给出配置值与缺失的具体工具名。
    """
    if pg_bin is None:
        return None
    path = Path(pg_bin).expanduser()
    return path.parent if path.is_file() else path


def resolve_backup_dir(settings: "Settings") -> Path:
    """迁移包默认备份目录（P010）：数据目录 > 存储目录旁 > 当前目录。"""
    if settings.backup_dir:
        return Path(settings.backup_dir).expanduser()
    if settings.data_dir:
        return Path(settings.data_dir).expanduser() / "backups"
    return Path(settings.storage_dir).expanduser().resolve().parent / "backups"
