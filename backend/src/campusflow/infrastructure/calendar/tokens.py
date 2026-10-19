"""OAuth 令牌加密。密钥只来自后端配置，接口和日志永不返回明文令牌。"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from campusflow.domain.errors import DomainError


class TokenCipher:
    """Fernet 对称加解密；密钥缺失时拒绝读写，不回退明文存储。

    密文进入数据库，明文只在单次请求内存在，接口与日志都不返回令牌。
    """

    def __init__(self, key: str | None) -> None:
        self._fernet = Fernet(key.encode("utf-8")) if key else None

    @property
    def configured(self) -> bool:
        return self._fernet is not None

    def encrypt(self, value: str) -> str:
        """加密令牌；未配置密钥时抛领域错误而不是保存明文。"""
        if self._fernet is None:
            raise DomainError(
                "日历凭据加密密钥未配置，请设置 CAMPUSFLOW_CALENDAR_TOKEN_KEY 后再连接。"
            )
        return self._fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt(self, value: str | None) -> str | None:
        """解密密文；密钥更换或密文损坏时提示重新授权，不静默返回原文。"""
        if value is None:
            return None
        if self._fernet is None:
            raise DomainError("日历凭据加密密钥未配置，无法读取已保存的授权，请设置后重启服务。")
        try:
            return self._fernet.decrypt(value.encode("utf-8")).decode("utf-8")
        except InvalidToken as error:
            raise DomainError("日历凭据无法解密，请撤销连接后重新授权。") from error
