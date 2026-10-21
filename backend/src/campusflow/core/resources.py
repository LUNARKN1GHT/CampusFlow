"""运行资源定位：打包或桌面模式不依赖进程工作目录与仓库相对位置。

迁移脚本可能来自环境变量显式路径、PyInstaller 解包目录或安装包内的
campusflow/migrations；源码运行时回退到 backend/migrations。
"""

import sys
from pathlib import Path

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
