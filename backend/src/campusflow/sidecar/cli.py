"""`campusflow-sidecar` 命令入口：serve／worker／migrate／backup／restore／doctor。

约定：0 成功，1 运行失败，2 参数使用错误。每个事件都写成一行 JSON 送到 stderr
（可选同时写 `--log-file`），stdout 只放命令结果，便于 Electron 直接读取与展示。
所有输出在写出前统一脱敏，不打印口令、密钥或连接串原文。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from campusflow.core.config import Settings
from campusflow.sidecar.backup import run_backup, run_restore
from campusflow.sidecar.doctor import run_doctor
from campusflow.sidecar.errors import SidecarError, UsageError
from campusflow.sidecar.logs import EventLogger, build_log_file, scrub_text
from campusflow.sidecar.migrate import run_migrate
from campusflow.sidecar.serve import DEFAULT_HOST, DEFAULT_PORT, run_serve
from campusflow.sidecar.worker import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_POLL_INTERVAL_SECONDS,
    MODES,
    run_worker,
)

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("campusflow-backend")
    except Exception:  # 源码运行且未安装时没有元数据
        return "0.1.0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="campusflow-sidecar",
        description="CampusFlow 桌面 sidecar：启动服务、运行 Worker、迁移、备份与诊断",
    )
    parser.add_argument("--version", action="version", version=f"campusflow-sidecar {_version()}")
    subcommands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    # 公共参数放在父解析器里，各子命令写法一致。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--log-file", help="同时把结构化日志追加写入该文件")

    serve = subcommands.add_parser("serve", parents=[common], help="启动本机 API 服务")
    serve.add_argument("--host", default=DEFAULT_HOST, help=f"监听地址（默认 {DEFAULT_HOST}）")
    serve.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"监听端口（默认 {DEFAULT_PORT}）"
    )

    worker = subcommands.add_parser("worker", parents=[common], help="运行后台作业 Worker")
    worker.add_argument(
        "--mode",
        choices=MODES,
        default="auto",
        help="auto 跟随 CAMPUSFLOW_DEPLOYMENT_MODE：desktop 用数据库轮询，server 用 RQ",
    )
    worker.add_argument("--burst", action="store_true", help="处理完当前可领取作业后退出")
    worker.add_argument(
        "--demo", action="store_true", help="仅限测试库：假识别与假向量，不调用模型"
    )
    worker.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help="桌面模式空闲轮询间隔秒数",
    )
    worker.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="桌面模式每批读取的作业数",
    )

    migrate = subcommands.add_parser("migrate", parents=[common], help="升级数据库结构到 head")
    migrate.add_argument("--check", action="store_true", help="只核对是否已是最新，不写库")
    migrate.add_argument("--json", action="store_true", help="以 JSON 输出迁移状态")

    backup = subcommands.add_parser("backup", parents=[common], help="备份数据库与资料存储")
    backup.add_argument("--output", required=True, help="归档输出路径（.tar）")
    backup.add_argument("--overwrite", action="store_true", help="允许覆盖已存在的归档文件")

    restore = subcommands.add_parser("restore", parents=[common], help="从归档恢复")
    restore.add_argument("archive", help="backup 生成的归档路径")
    restore.add_argument(
        "--replace",
        action="store_true",
        help="目标数据库已有数据时覆盖；会先写一份安全备份",
    )
    restore.add_argument("--safety-backup", help="覆盖前安全备份的输出路径")
    restore.add_argument(
        "--strategy",
        choices=("auto", "direct"),
        default="auto",
        help="auto 先尝试临时库再改名；direct 直接 pg_restore --clean",
    )

    doctor = subcommands.add_parser("doctor", parents=[common], help="只读诊断运行环境")
    doctor.add_argument("--json", action="store_true", help="以 JSON 输出诊断结果")
    return parser


def load_settings() -> Settings:
    """按环境变量与 .env 装配配置；失败只提示可检查的项，不回显取值。"""
    try:
        return Settings()
    except Exception:
        raise SidecarError(
            "配置无效：请检查 CAMPUSFLOW_* 环境变量"
            "（桌面模式必须提供绝对路径的 CAMPUSFLOW_DATA_DIR）"
        ) from None


def _secrets(settings: Settings) -> list[str | None]:
    """兜底替换项只放模型密钥：数据库口令从不进入输出，替换它反而会打碎库名。"""
    return [settings.zhipu_api_key, settings.deepseek_api_key]


def _emit(text: str, settings: Settings) -> None:
    """stdout 统一出口：写之前再脱敏一次，避免任何调用路径意外带出密钥。"""
    print(scrub_text(text, _secrets(settings)))


def _format_revisions(revisions: list[str]) -> str:
    return ",".join(revisions) if revisions else "（空）"


def _handle_serve(args: argparse.Namespace, settings: Settings, logger: EventLogger) -> int:
    return run_serve(settings, logger, host=args.host, port=args.port)


def _handle_worker(args: argparse.Namespace, settings: Settings, logger: EventLogger) -> int:
    return run_worker(
        settings,
        logger,
        mode=args.mode,
        burst=args.burst,
        demo=args.demo,
        poll_interval=args.poll_interval,
        batch_size=args.batch_size,
    )


def _handle_migrate(args: argparse.Namespace, settings: Settings, logger: EventLogger) -> int:
    result = run_migrate(settings, logger, check=args.check)
    if args.check:
        state = "已是最新" if result["at_head"] else "未就绪"
        if args.json:
            _emit(json.dumps(result, ensure_ascii=False), settings)
        else:
            _emit(
                f"迁移{state}：current={_format_revisions(result['current'])} "
                f"head={_format_revisions(result['head'])}",
                settings,
            )
        return EXIT_OK if result["at_head"] else EXIT_FAILURE
    if args.json:
        _emit(json.dumps(result, ensure_ascii=False), settings)
    else:
        _emit(
            f"迁移完成：{_format_revisions(result['before'])} -> "
            f"{_format_revisions(result['after'])}"
            f"（head={_format_revisions(result['head'])}）",
            settings,
        )
    return EXIT_OK


def _handle_backup(args: argparse.Namespace, settings: Settings, logger: EventLogger) -> int:
    result = run_backup(settings, logger, args.output, overwrite=args.overwrite)
    _emit(
        f"备份完成：{result.path}（{result.archive_bytes} 字节，"
        f"资料文件 {result.storage_files} 个，迁移头 {_format_revisions(result.alembic_head)}）",
        settings,
    )
    return EXIT_OK


def _handle_restore(args: argparse.Namespace, settings: Settings, logger: EventLogger) -> int:
    result = run_restore(
        settings,
        logger,
        args.archive,
        replace=args.replace,
        safety_backup=args.safety_backup,
        strategy=args.strategy,
    )
    safety = f"，安全备份 {result.safety_backup}" if result.safety_backup else ""
    _emit(
        f"恢复完成：{result.database}（{result.mode}，资料文件 {result.storage_files} 个{safety}）",
        settings,
    )
    return EXIT_OK


def _handle_doctor(args: argparse.Namespace, settings: Settings, logger: EventLogger) -> int:
    log_dir = Path(args.log_file).expanduser().absolute().parent if args.log_file else None
    report = run_doctor(settings, logger, log_dir=log_dir)
    if args.json:
        _emit(json.dumps(report, ensure_ascii=False, indent=2), settings)
    else:
        lines = [f"诊断结果：{'通过' if report['ok'] else '失败'}"]
        for name, check in report["checks"].items():
            if check["ok"]:
                marker = "ok"
            else:
                marker = "失败" if check["critical"] else "警告"
            detail = check.get("version") or check.get("status") or ""
            lines.append(f"  {name}: {marker} {detail}".rstrip())
        if report["critical_failed"]:
            lines.append("关键检查失败：" + "、".join(report["critical_failed"]))
        if report["warnings"]:
            lines.append("提示：" + "、".join(report["warnings"]) + "（不阻止启动）")
        _emit("\n".join(lines), settings)
    return EXIT_OK if report["ok"] else EXIT_FAILURE


_HANDLERS = {
    "serve": _handle_serve,
    "worker": _handle_worker,
    "migrate": _handle_migrate,
    "backup": _handle_backup,
    "restore": _handle_restore,
    "doctor": _handle_doctor,
}


def main(argv: list[str] | None = None) -> int:
    """命令分发；返回退出码而不是直接退出，便于测试与 Electron 复用。"""
    arguments = list(sys.argv[1:]) if argv is None else list(argv)
    parser = build_parser()
    try:
        args = parser.parse_args(arguments)
    except SystemExit as exc:  # argparse 已把用法写到 stderr，这里只传递退出码
        return int(exc.code or EXIT_OK)

    try:
        log_file = build_log_file(args.log_file) if args.log_file else None
    except OSError:
        print("无法写入日志文件：请检查路径与权限", file=sys.stderr)
        return EXIT_FAILURE

    logger = EventLogger(args.command, log_file=log_file)
    logger.info("command_started", argv=arguments)
    try:
        settings = load_settings()
        exit_code = _HANDLERS[args.command](args, settings, logger)
    except UsageError as exc:
        logger.error("usage_error", message=str(exc))
        print(str(exc), file=sys.stderr)
        exit_code = EXIT_USAGE
    except SidecarError as exc:
        logger.error("command_failed", message=str(exc))
        print(str(exc), file=sys.stderr)
        exit_code = EXIT_FAILURE
    except SQLAlchemyError:
        logger.error("command_failed", error="database_unavailable")
        print("无法连接数据库：请检查 CAMPUSFLOW_DATABASE_URL 与数据库进程", file=sys.stderr)
        exit_code = EXIT_FAILURE
    except Exception as exc:  # 不回显异常原文，避免连接串或资料内容进日志
        logger.error("command_failed", error=type(exc).__name__)
        print("命令执行失败：请运行 doctor 检查配置与服务", file=sys.stderr)
        exit_code = EXIT_FAILURE
    finally:
        logger.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
