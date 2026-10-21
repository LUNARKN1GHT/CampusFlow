"""本机凭据后端装配（P007）。

auto：macOS 用系统钥匙串，Windows 用 DPAPI，其它平台回退到 0600 文件；
显式指定的后端不可用时直接报错，不静默换成其它实现（避免以为走的是密钥链）。
"""

from pathlib import Path

from campusflow.application.ports.secrets import SecretStore
from campusflow.infrastructure.secrets.dpapi import WindowsDpapiSecretStore
from campusflow.infrastructure.secrets.file import FileSecretStore
from campusflow.infrastructure.secrets.keychain import KeychainSecretStore

SECRET_BACKEND_CHOICES = ("auto", "keychain", "dpapi", "file")


class SecretBackendError(RuntimeError):
    """凭据后端不可用（系统不支持或工具缺失）；不包含任何凭据内容。"""


def create_secret_store(name: str, root: Path) -> SecretStore:
    """按后端名创建凭据存储；文件后端落在 `<root>/secrets.json`。"""
    normalized = (name or "auto").strip().lower()
    if normalized == "auto":
        if KeychainSecretStore.is_available():
            return KeychainSecretStore()
        if WindowsDpapiSecretStore.is_available():
            return WindowsDpapiSecretStore(root / "secrets.dpapi")
        return FileSecretStore(root / "secrets.json")
    if normalized in ("keychain", "macos-keychain"):
        if not KeychainSecretStore.is_available():
            raise SecretBackendError("当前系统没有可用的 macOS 钥匙串（security 命令）")
        return KeychainSecretStore()
    if normalized in ("dpapi", "windows-dpapi"):
        if not WindowsDpapiSecretStore.is_available():
            raise SecretBackendError("当前系统不支持 Windows DPAPI")
        return WindowsDpapiSecretStore(root / "secrets.dpapi")
    if normalized == "file":
        return FileSecretStore(root / "secrets.json")
    raise SecretBackendError(f"未知的凭据后端：{name}")


__all__ = [
    "SECRET_BACKEND_CHOICES",
    "FileSecretStore",
    "KeychainSecretStore",
    "SecretBackendError",
    "WindowsDpapiSecretStore",
    "create_secret_store",
]
