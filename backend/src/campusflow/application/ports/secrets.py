"""本机凭据存取端口（P007）。

桌面模式的数据库口令与本地登录口令只写系统密钥链或受保护的本地文件，
不进入 state.json、日志和仓库；实现必须在 repr/日志中隐藏明文。
"""

import secrets
from typing import Protocol


class SecretStore(Protocol):
    """按账户名存取本机凭据；`backend` 是写入 state.json 的后端标识。"""

    @property
    def backend(self) -> str:
        """稳定的后端标识（如 macos-keychain、file），用于 state.json 与诊断输出。"""
        ...

    def get(self, account: str) -> str | None:
        """读取凭据；不存在返回 None，读取失败必须抛错而不是伪装成缺失。"""
        ...

    def set(self, account: str, value: str) -> None:
        """写入或覆盖凭据；不得把明文写进日志或异常信息。"""
        ...

    def delete(self, account: str) -> bool:
        """删除凭据；存在并删除返回 True。"""
        ...


class Secret:
    """凭据明文包装：str()/repr() 只输出掩码，取值必须显式 `reveal()`。

    这样可以安全地把凭据放进报告或日志，而误用 f-string 也不会泄露明文。
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not value:
            raise ValueError("凭据不能为空")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def matches(self, other: str) -> bool:
        return secrets.compare_digest(self._value, other)

    def __repr__(self) -> str:
        return "Secret(***)"

    def __str__(self) -> str:
        return "***"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Secret) and secrets.compare_digest(self._value, other._value)

    __hash__ = None  # type: ignore[assignment]
