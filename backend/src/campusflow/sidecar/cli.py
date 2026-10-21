"""`campusflow-sidecar` 命令入口：serve／worker／migrate／backup／restore／doctor／
bootstrap／export／import／upgrade。

约定：0 成功，1 运行失败，2 参数使用错误。每个事件都写成一行 JSON 送到 stderr
（可选同时写 `--log-file`），stdout 只放命令结果，便于 Electron 直接读取与展示。
所有输出在写出前统一脱敏，不打印口令、密钥或连接串原文；唯一例外是
`bootstrap --json`：Electron 自动登录需要本地口令与连接串，只出现在这一次的
stdout 上，不进入 stderr、日志文件或 state.json。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from campusflow.core.config import Settings
from campusflow.sidecar.backup import run_backup, run_restore
from campusflow.sidecar.bootstrap import run_bootstrap
from campusflow.sidecar.doctor import run_doctor
from campusflow.sidecar.errors import SidecarError, UsageError
from campusflow.sidecar.logs import EventLogger, build_log_file, scrub_text
from campusflow.sidecar.migrate import run_migrate
from campusflow.sidecar.serve import DEFAULT_HOST, DEFAULT_PORT, run_serve
from campusflow.sidecar.transfer import inspect_archive_payload, run_export, run_import
from campusflow.sidecar.upgrade import apply_upgrade, check_upgrade, describe
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

    bootstrap = subcommands.add_parser(
        "bootstrap", parents=[common], help="桌面模式首启初始化与就绪（仅 desktop）"
    )
    bootstrap.add_argument("--json", action="store_true", help="输出含连接信息的 JSON")
    bootstrap.add_argument(
        "--check",
        action="store_true",
        help="只读检查数据目录状态：不初始化、不迁移、不启动 PostgreSQL",
    )

    export = subcommands.add_parser(
        "export", parents=[common], help="导出整库与资料为 .campusflow 归档"
    )
    export.add_argument("--output", required=True, help="归档输出路径（.campusflow）")
    export.add_argument(
        "--workspace-id",
        type=int,
        help="首版只支持整库导出；传入该参数会被明确拒绝",
    )

    imports = subcommands.add_parser(
        "import", parents=[common], help="从 .campusflow 归档导入（需确认令牌）"
    )
    imports.add_argument("archive", help="inspect 或导出的归档路径")
    imports.add_argument("--inspect", action="store_true", help="只读检查归档，不修改数据")
    imports.add_argument(
        "--replace", action="store_true", help="目标库已有数据时整体替换（须同时给令牌）"
    )
    imports.add_argument(
        "--confirm-token",
        help="import --inspect 返回的 confirm_token；只接受命令行参数，不读环境变量",
    )

    upgrade = subcommands.add_parser(
        "upgrade", parents=[common], help="按 P011 备份后升级数据库结构（不降级）"
    )
    upgrade.add_argument("--check", action="store_true", help="只读检查升级状态，不写库")
    upgrade.add_argument(
        "--apply",
        action="store_true",
        help="配合 --check：需要升级时执行正式升级（先备份再迁移）",
    )
    upgrade.add_argument("--json", action="store_true", help="以 JSON 输出结果")
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


def _emit_payload(payload: dict[str, Any], settings: Settings) -> None:
    """JSON 输出：只替换模型密钥原文，不做 URL 级脱敏。

    `bootstrap --json` 必须把本地口令与含口令的连接串交给 Electron 自动登录，
    因此这里保留连接信息；这是它唯一允许出现口令的出口（只写 stdout）。
    其它命令的 payload 本来就不含口令，兜底替换模型密钥仍然生效。
    """
    text = json.dumps(payload, ensure_ascii=False, default=str)
    for secret in _secrets(settings):
        if secret and len(secret) >= 4:
            text = text.replace(secret, "***")
    print(text)


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


def _bootstrap_summary(payload: dict[str, Any], *, check: bool) -> str:
    """非 JSON 输出：短中文状态，绝不打印本地口令或连接串。"""
    data_dir = payload["data_dir"]
    head = payload["schema_head"] or "未迁移"
    if check:
        if payload["initialized"]:
            phrase = "PostgreSQL 正在运行" if payload["postgres_running"] else "PostgreSQL 未运行"
            running = f"，{phrase}"
            lines = [f"数据目录已初始化：{data_dir}（迁移头 {head}{running}）"]
        else:
            lines = [
                f"数据目录未就绪：{payload['status']}（{data_dir}）；"
                "运行 bootstrap 完成初始化并启动数据库"
            ]
    else:
        started = "，本次已启动 PostgreSQL" if payload["started_postgres"] else ""
        lines = [f"桌面数据目录已就绪：{data_dir}（迁移头 {head}{started}）"]
    if payload["warnings"]:
        lines.append("提示：" + "；".join(payload["warnings"]))
    return "\n".join(lines)


def _handle_bootstrap(args: argparse.Namespace, settings: Settings, logger: EventLogger) -> int:
    payload = run_bootstrap(settings, logger, check=args.check)
    if args.json:
        _emit_payload(payload, settings)
    else:
        _emit(_bootstrap_summary(payload, check=args.check), settings)
    if args.check:
        # `--check` 只证明数据目录可用（initialized）；是否真的能连库由随后的运行确认。
        return EXIT_OK if payload["initialized"] else EXIT_FAILURE
    return EXIT_OK


def _handle_export(args: argparse.Namespace, settings: Settings, logger: EventLogger) -> int:
    payload = run_export(settings, logger, args.output, workspace_id=args.workspace_id)
    _emit_payload(payload, settings)
    return EXIT_OK


def _handle_import(args: argparse.Namespace, settings: Settings, logger: EventLogger) -> int:
    if args.inspect:
        if args.confirm_token or args.replace:
            raise UsageError("--inspect 只读检查归档，不能与 --replace 或 --confirm-token 同时使用")
        payload = inspect_archive_payload(settings, args.archive)
    else:
        payload = run_import(
            settings,
            logger,
            args.archive,
            replace=args.replace,
            confirm_token=args.confirm_token,
        )
    _emit_payload(payload, settings)
    return EXIT_OK


def _handle_upgrade(args: argparse.Namespace, settings: Settings, logger: EventLogger) -> int:
    if args.check:
        payload, exit_code = check_upgrade(settings, logger, apply=args.apply)
    else:
        payload = apply_upgrade(settings, logger)
        exit_code = EXIT_OK
    if args.json:
        _emit_payload(payload, settings)
    else:
        _emit(describe(payload), settings)
    return exit_code


_HANDLERS = {
    "serve": _handle_serve,
    "worker": _handle_worker,
    "migrate": _handle_migrate,
    "backup": _handle_backup,
    "restore": _handle_restore,
    "doctor": _handle_doctor,
    "bootstrap": _handle_bootstrap,
    "export": _handle_export,
    "import": _handle_import,
    "upgrade": _handle_upgrade,
}


def _configure_streams() -> None:
    """Windows 控制台默认不是 UTF-8，中文 JSON 与错误提示会触发 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            continue


def main(argv: list[str] | None = None) -> int:
    """命令分发；返回退出码而不是直接退出，便于测试与 Electron 复用。"""
    _configure_streams()
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
