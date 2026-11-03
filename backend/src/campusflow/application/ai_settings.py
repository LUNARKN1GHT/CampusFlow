"""桌面模型配置整份写入凭据存储，公开结果不含密钥。"""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from campusflow.application.ports.secrets import SecretStore
from campusflow.core.config import Settings
from campusflow.domain.errors import DomainError


class AISettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text_provider: Literal["zhipu", "deepseek"]
    text_model: str = Field(min_length=1, max_length=100, pattern=r"^[\w.:-]+$")
    vision_model: str = Field(min_length=1, max_length=100, pattern=r"^[\w.:-]+$")
    # null 保留，空字符串清除；页面永不读取已存密钥。
    zhipu_api_key: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None

    @field_validator("zhipu_api_key", "deepseek_api_key")
    @classmethod
    def validate_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            raw = value.get_secret_value()
            if len(raw) > 512 or any(c.isspace() for c in raw):
                raise ValueError("密钥格式无效")
        return value


def read_settings(base: Settings, store: SecretStore, account: str) -> Settings:
    try:
        raw = store.get(account)
        if raw is None:
            return base
        config = AISettingsUpdate.model_validate_json(raw)
        values = config.model_dump(exclude={"zhipu_api_key", "deepseek_api_key"})
        for name in ("zhipu_api_key", "deepseek_api_key"):
            key = getattr(config, name)
            values[name] = key.get_secret_value() if key is not None else None
        return base.model_copy(update=values)
    except Exception:
        raise DomainError("无法读取 AI 服务设置，请检查本机凭据存储；未使用旧密钥代替。") from None


def save_settings(
    base: Settings, store: SecretStore, account: str, update: AISettingsUpdate
) -> Settings:
    current = read_settings(base, store, account)
    values = update.model_dump(exclude={"zhipu_api_key", "deepseek_api_key"})
    for name in ("zhipu_api_key", "deepseek_api_key"):
        key = getattr(update, name)
        values[name] = getattr(current, name) if key is None else key.get_secret_value()
    try:
        store.set(account, json.dumps(values, ensure_ascii=False))
    except Exception:
        raise DomainError("AI 服务设置未能安全保存，请检查本机凭据存储后重试。") from None
    return read_settings(base, store, account)


def public_settings(settings: Settings) -> dict:
    zhipu = bool(settings.zhipu_api_key)
    text_ready = bool(settings.deepseek_api_key) if settings.text_provider == "deepseek" else zhipu
    return {
        "editable": settings.deployment_mode == "desktop",
        "text_provider": settings.text_provider,
        "text_model": settings.text_model,
        "vision_model": settings.vision_model,
        "embedding_model": settings.embedding_model,
        "embedding_dimensions": settings.embedding_dimensions,
        "zhipu_configured": zhipu,
        "deepseek_configured": bool(settings.deepseek_api_key),
        "capabilities": {"text": text_ready, "vision": zhipu, "embedding": zhipu},
    }
