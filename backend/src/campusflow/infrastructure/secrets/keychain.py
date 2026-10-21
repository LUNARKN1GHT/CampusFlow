"""macOS 系统钥匙串凭据（P007）。

通过 `security` 命令读写通用口令项：服务名固定，账户名区分用途。
写入口令经 stdin 传入（两次，对应输入与确认），不出现在进程参数里；
读取失败只有「条目不存在」按缺失处理，其它错误原样抛出，不伪装成缺失。
"""

from __future__ import annotations

import shutil
import subprocess
import sys

BACKEND_NAME = "macos-keychain"
SERVICE_NAME = "campusflow-desktop"
_COMMAND_TIMEOUT_SECONDS = 20
_ITEM_NOT_FOUND_EXIT_CODE = 44


class KeychainError(RuntimeError):
    """钥匙串不可用或读写失败；消息不含凭据内容。"""


class KeychainSecretStore:
    """通用口令项：service=`campusflow-desktop`，account 由调用方给定。"""

    def __init__(self, service: str = SERVICE_NAME) -> None:
        binary = shutil.which("security")
        if binary is None:
            raise KeychainError("未找到 security 命令，无法使用 macOS 钥匙串")
        self._binary = binary
        self._service = service

    @staticmethod
    def is_available() -> bool:
        return sys.platform == "darwin" and shutil.which("security") is not None

    @property
    def backend(self) -> str:
        return BACKEND_NAME

    def _run(self, args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [self._binary, *args],
                capture_output=True,
                text=True,
                input=stdin,
                timeout=_COMMAND_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:  # 不把异常原文（可能含路径）当成凭据
            raise KeychainError("调用 security 命令失败") from exc

    def get(self, account: str) -> str | None:
        result = self._run(
            ["find-generic-password", "-s", self._service, "-a", account, "-w"]
        )
        if result.returncode == 0:
            return result.stdout.rstrip("\n")
        if result.returncode == _ITEM_NOT_FOUND_EXIT_CODE or "could not be found" in (
            result.stderr or ""
        ).lower():
            return None
        raise KeychainError("读取钥匙串条目失败")

    def set(self, account: str, value: str) -> None:
        # -U 覆盖同名项；口令与确认口令都从 stdin 读入，argv 只含账户名。
        result = self._run(
            ["add-generic-password", "-U", "-s", self._service, "-a", account, "-w"],
            stdin=f"{value}\n{value}\n",
        )
        if result.returncode != 0:
            raise KeychainError("写入钥匙串条目失败")

    def delete(self, account: str) -> bool:
        result = self._run(["delete-generic-password", "-s", self._service, "-a", account])
        if result.returncode == 0:
            return True
        if result.returncode == _ITEM_NOT_FOUND_EXIT_CODE or "could not be found" in (
            result.stderr or ""
        ).lower():
            return False
        raise KeychainError("删除钥匙串条目失败")
