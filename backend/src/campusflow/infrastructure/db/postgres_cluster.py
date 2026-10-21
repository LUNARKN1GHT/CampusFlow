"""本机 PostgreSQL 集群生命周期（P007）。

只调用 `initdb`、`pg_ctl` 与 `pg_isready`。二进制目录由 `CAMPUSFLOW_PG_BIN` 指定，
未指定时使用 PATH，不假设版本号：本地开发是 16，打包分发是 17，命令行参数一致。

所有子进程不经过 shell；口令只经 `--pwfile` 传入，不出现在命令行参数里。
在 macOS 上启动服务器前把 locale 固定为 C：否则 postmaster 会因进程继承到多线程
状态而报 "postmaster became multithreaded during startup" 并退出。
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

# macOS 的 sun_path 上限是 104 字节；预留 `.s.PGSQL.<port>` 与结尾零字节后取 100。
_SOCKET_PATH_LIMIT = 100
_RUNTIME_CONFIG_NAME = "campusflow.conf"
_COMMAND_TIMEOUT_SECONDS = 180
_START_WAIT_SECONDS = 60
_STOP_WAIT_SECONDS = 30
_LOG_TAIL_LINES = 40


class PostgresError(RuntimeError):
    """PostgreSQL 工具缺失或命令失败；消息只含可诊断输出，不含口令。"""


def _tail(text: str | None, lines: int = _LOG_TAIL_LINES) -> str:
    if not text:
        return "（无输出）"
    return "\n".join(text.strip().splitlines()[-lines:])


def _run(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: int = _COMMAND_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PostgresError(f"无法执行 {Path(command[0]).name}") from exc


def server_env() -> dict[str, str]:
    """服务器与 pg_ctl 的环境：固定 C locale，不传递任何 PGPASSWORD。"""
    env = {key: value for key, value in os.environ.items() if key != "PGPASSWORD"}
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    return env


@dataclass(frozen=True)
class PgBinaries:
    """initdb／pg_ctl／pg_isready 所在目录与版本字符串。"""

    bindir: Path
    version: str

    @classmethod
    def discover(cls, pg_bin: str | None = None) -> PgBinaries:
        bindir = Path(pg_bin).expanduser() if pg_bin else None
        if bindir is None:
            found = shutil.which("initdb")
            if found is None:
                raise PostgresError(
                    "未找到 initdb：请安装 PostgreSQL，或用 CAMPUSFLOW_PG_BIN 指定二进制目录"
                )
            bindir = Path(found).parent
        resolved = {name: _binary(bindir, name) for name in ("initdb", "pg_ctl", "pg_isready")}
        missing = [name for name, path in resolved.items() if not path.is_file()]
        if missing:
            raise PostgresError(f"PostgreSQL 二进制目录 {bindir} 缺少：{', '.join(missing)}")
        result = _run([str(resolved["initdb"]), "--version"])
        if result.returncode != 0:
            raise PostgresError(f"无法读取 initdb 版本：{_tail(result.stderr)}")
        output = (result.stdout or result.stderr).strip()
        match = re.search(r"\d+(?:\.\d+)+", output)
        if match is None:
            raise PostgresError(f"无法识别 initdb 版本输出：{_tail(output, 1)}")
        return cls(bindir=bindir, version=match.group(0))

    @classmethod
    def available(cls, pg_bin: str | None = None) -> bool:
        try:
            cls.discover(pg_bin)
        except PostgresError:
            return False
        return True

    def tool(self, name: str) -> Path:
        return _binary(self.bindir, name)


def _binary(bindir: Path, name: str) -> Path:
    """Windows 官方安装包只提供 `*.exe`，按实际存在的文件名解析。"""
    candidate = bindir / name
    if candidate.is_file():
        return candidate
    suffixed = bindir / f"{name}.exe"
    return suffixed if suffixed.is_file() else candidate


def find_free_port(host: str = "127.0.0.1") -> int:
    """让系统分配一个空闲回环端口；绑定后立即释放，由 PostgreSQL 紧接着占用。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def socket_directory(data_dir: Path, port: int) -> tuple[Path, bool]:
    """返回（套接字目录，是否为路径过长的临时回退）。

    优先使用数据目录内的 `run`；Unix 套接字路径超长时退回系统临时目录，
    否则 PostgreSQL 会因 sun_path 截断而启动失败。回环 TCP 始终是主要连接方式。
    """
    preferred = Path(data_dir) / "run"
    if len(os.fsencode(preferred / f".s.PGSQL.{port}")) <= _SOCKET_PATH_LIMIT:
        return preferred, False
    base = Path("/tmp") if Path("/tmp").is_dir() else Path(tempfile.gettempdir())
    return Path(tempfile.mkdtemp(prefix="campusflow-pgsock-", dir=base)), True


def _quote_conf_value(value: str) -> str:
    return value.replace("'", "''")


class LocalPostgresCluster:
    """单个数据目录上的 PostgreSQL 服务器（不注册系统服务）。"""

    def __init__(
        self,
        data_dir: Path,
        binaries: PgBinaries,
        *,
        port: int,
        socket_dir: Path,
        log_file: Path,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.binaries = binaries
        self.port = port
        self.socket_dir = Path(socket_dir)
        self.log_file = Path(log_file)

    def initdb(self, superuser: str, password: str) -> None:
        """初始化集群并写入连接配置；口令文件用后立即删除。"""
        self.data_dir.parent.mkdir(parents=True, exist_ok=True)
        handle, password_name = tempfile.mkstemp(dir=self.data_dir.parent, prefix=".pgpass-")
        password_file = Path(password_name)
        try:
            os.chmod(password_file, 0o600)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(password)
            result = _run(
                [
                    str(self.binaries.tool("initdb")),
                    "-D",
                    str(self.data_dir),
                    "-U",
                    superuser,
                    "--locale=C",
                    "--encoding=UTF8",
                    "--auth=scram-sha-256",
                    f"--pwfile={password_file}",
                ]
            )
        finally:
            password_file.unlink(missing_ok=True)
        if result.returncode != 0:
            raise PostgresError(f"initdb 失败：{_tail(result.stderr)}")
        self._write_config()

    def _write_config(self) -> None:
        # 端口与套接字目录写在独立 include 文件里：首启在临时目录初始化，成功后
        # 整个目录会被改名，固定路径写进 postgresql.conf 会指向已不存在的目录。
        # 每次启动前重写，保证与当前数据目录一致。
        self.socket_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.socket_dir, 0o700)
        lines = [
            "# CampusFlow 桌面模式配置（P007，每次启动前重写）",
            "listen_addresses = '127.0.0.1'",
            f"port = {self.port}",
            f"unix_socket_directories = '{_quote_conf_value(str(self.socket_dir))}'",
            "unix_socket_permissions = 0700",
            "password_encryption = scram-sha-256",
            "",
        ]
        runtime_config = self.data_dir / _RUNTIME_CONFIG_NAME
        runtime_config.write_text("\n".join(lines), encoding="utf-8")
        main_config = self.data_dir / "postgresql.conf"
        include_line = f"include_if_exists = '{_RUNTIME_CONFIG_NAME}'"
        existing = main_config.read_text(encoding="utf-8") if main_config.is_file() else ""
        if include_line not in existing:
            with main_config.open("a", encoding="utf-8") as stream:
                stream.write(f"\n# CampusFlow 桌面模式附加配置（P007）\n{include_line}\n")

    def start(self) -> None:
        try:
            self._write_config()
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PostgresError("无法写入 PostgreSQL 运行配置，数据目录可能不可写") from exc
        result = _run(
            [
                str(self.binaries.tool("pg_ctl")),
                "-D",
                str(self.data_dir),
                "-l",
                str(self.log_file),
                "-w",
                "-t",
                str(_START_WAIT_SECONDS),
                "start",
            ],
            env=server_env(),
        )
        if result.returncode != 0:
            raise PostgresError(f"PostgreSQL 启动失败：{self.log_tail()}")

    def stop(self) -> bool:
        """停止服务器；本来没运行返回 False，停止失败抛错。"""
        if not self.is_running():
            return False
        result = _run(
            [
                str(self.binaries.tool("pg_ctl")),
                "-D",
                str(self.data_dir),
                "-m",
                "fast",
                "-w",
                "-t",
                str(_STOP_WAIT_SECONDS),
                "stop",
            ],
            env=server_env(),
        )
        if result.returncode != 0 and self.is_running():
            raise PostgresError(f"PostgreSQL 停止失败：{_tail(result.stderr)}")
        return result.returncode == 0

    def is_running(self) -> bool:
        if not (self.data_dir / "PG_VERSION").is_file():
            return False
        result = _run([str(self.binaries.tool("pg_ctl")), "-D", str(self.data_dir), "status"])
        return result.returncode == 0

    def wait_ready(self, host: str = "127.0.0.1", timeout: int = _START_WAIT_SECONDS) -> None:
        """等待回环 TCP 真正接受连接；pg_ctl -w 只保证进程启动。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = _run(
                [
                    str(self.binaries.tool("pg_isready")),
                    "-h",
                    host,
                    "-p",
                    str(self.port),
                    "-q",
                ],
                timeout=10,
            )
            if result.returncode == 0:
                return
            time.sleep(0.2)
        raise PostgresError(f"PostgreSQL 未在 {timeout} 秒内接受连接：{self.log_tail()}")

    def log_tail(self) -> str:
        try:
            text = self.log_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "（无日志）"
        return _tail(text)
