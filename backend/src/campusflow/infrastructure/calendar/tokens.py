"""OAuth 令牌加密。密钥只来自后端配置，接口和日志永不返回明文令牌。"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from campusflow.domain.errors import DomainError


class TokenCipher:
    def __init__(self, key: str | None) -> None:
        self._fernet = Fernet(key.encode("utf-8")) if key else None

    @property
    def configured(self) -> bool:
        return self._fernet is not None

    def encrypt(self, value: str) -> str:
        if self._fernet is None:
            raise DomainError(
                "日历凭据加密密钥未配置，请设置 CAMPUSFLOW_CALENDAR_TOKEN_KEY 后再连接。"
            )
        return self._fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        if self._fernet is None:
            raise DomainError("日历凭据加密密钥未配置，无法读取已保存的授权，请设置后重启服务。")
        try:
            return self._fernet.decrypt(value.encode("utf-8")).decode("utf-8")
        except InvalidToken as error:
            raise DomainError("日历凭据无法解密，请撤销连接后重新授权。") from error
