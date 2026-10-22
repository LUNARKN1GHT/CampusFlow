"""内存凭据存储（P007）：只用于测试与显式注入，进程结束即丢失。"""

from __future__ import annotations

BACKEND_NAME = "memory"


class InMemorySecretStore:
    """不落盘的凭据存储；不得用于正式启动路径。"""

    def __init__(self, backend: str = BACKEND_NAME) -> None:
        self._backend = backend
        self._values: dict[str, str] = {}

    @property
    def backend(self) -> str:
        return self._backend

    def get(self, account: str) -> str | None:
        return self._values.get(account)

    def set(self, account: str, value: str) -> None:
        self._values[account] = value

    def delete(self, account: str) -> bool:
        return self._values.pop(account, None) is not None
