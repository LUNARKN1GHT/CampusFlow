"""CampusFlow 桌面 sidecar 入口（P002a）。

打包后由桌面外壳或脚本拉起，不依赖仓库目录、Python 解释器或 uv。子命令：

- ``serve``：在本机 127.0.0.1 启动 FastAPI（uvicorn），端口可配置。
- ``migrate``：用随包附带的 Alembic 迁移升级数据库，不依赖仓库相对路径。
- ``worker``：启动现有 RQ Worker（需要 Redis）；P005/P006 会替换为桌面
  数据库轮询 Worker，本入口暂沿用服务端同一条装配路径。

资源定位只在冻结运行时使用 ``sys._MEIPASS``，源码运行时回退到
``backend/migrations``；没有从工作目录推断路径的分支。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 迁移目录候选顺序：显式参数 > PyInstaller 解包目录 > 源码仓库。
# 与 P004 的 core/resources.py 保持同一套解析思路，但不依赖该分支才能打包。
_BUNDLED_MIGRATION_DIRS = ("migrations", "backend/migrations", "campusflow/migrations")


def _migration_candidates(migrations_dir: str | None) -> list[Path]:
    if migrations_dir is not None:
        return [Path(migrations_dir).expanduser()]
    candidates: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root is not None:
        root = Path(bundle_root)
        candidates.extend(root / name for name in _BUNDLED_MIGRATION_DIRS)
    candidates.append(Path(__file__).resolve().parent.parent / "backend" / "migrations")
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


def build_alembic_config(migrations_dir: str | None = None):
    """按绝对 script_location 装配 Alembic，避免依赖 backend/ 工作目录。"""
    from alembic.config import Config

    script_location = resolve_migrations_dir(migrations_dir)
    ini_path = script_location.parent / "alembic.ini"
    config = Config(str(ini_path)) if ini_path.is_file() else Config()
    config.set_main_option("script_location", str(script_location))
    return config


def run_serve(args: argparse.Namespace) -> None:
    """只绑定回环地址，不对外监听；端口由桌面外壳或用户指定。"""
    import uvicorn

    from campusflow.main import create_app

    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level=args.log_level)


def run_migrate(args: argparse.Namespace) -> None:
    """使用随包迁移执行升级；打印解析到的目录便于核对不是仓库路径。"""
    from alembic import command

    config = build_alembic_config(args.migrations_dir)
    print(f"迁移目录：{config.get_main_option('script_location')}", flush=True)
    if args.revision == "current":
        command.current(config, verbose=True)
    else:
        command.upgrade(config, args.revision)


def run_worker(args: argparse.Namespace) -> None:
    """复用现有 RQ Worker 入口；参数保持最小，业务装配不在这里重复。"""
    from campusflow.workers import main as worker_main

    argv = ["campusflow-sidecar worker"]
    if args.burst:
        argv.append("--burst")
    if args.demo:
        argv.append("--demo")
    original = sys.argv
    sys.argv = argv
    try:
        worker_main.main()
    finally:
        sys.argv = original


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="campusflow-sidecar", description="CampusFlow 桌面包入口")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="启动本机 API（127.0.0.1）")
    serve.add_argument("--port", type=int, default=8000, help="监听端口，默认 8000")
    serve.add_argument("--log-level", default="info", help="uvicorn 日志级别，默认 info")
    serve.set_defaults(handler=run_serve)

    migrate = subparsers.add_parser("migrate", help="用随包迁移升级数据库")
    migrate.add_argument(
        "--revision", default="head", help="目标 revision，默认 head；current 只查询当前版本"
    )
    migrate.add_argument("--migrations-dir", default=None, help="显式迁移目录（默认随包资源）")
    migrate.set_defaults(handler=run_migrate)

    worker = subparsers.add_parser("worker", help="启动 RQ Worker（需要 Redis）")
    worker.add_argument("--burst", action="store_true", help="处理完当前队列后退出")
    worker.add_argument("--demo", action="store_true", help="仅限 *_test 测试库：假识别与假向量")
    worker.set_defaults(handler=run_worker)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
