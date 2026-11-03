"""API 和 Worker 使用同一桌面凭据记录；保存/清除后新调用无需重启。"""

import hashlib
from pathlib import Path

from campusflow.application.ai_settings import read_settings
from campusflow.core.config import Settings
from campusflow.domain.errors import DomainError
from campusflow.infrastructure.secrets import create_secret_store


def settings_store(settings: Settings):
    if settings.deployment_mode != "desktop" or not settings.data_dir:
        raise DomainError("网页版的 AI 配置由服务管理员维护；桌面版可在本页修改。")
    root = Path(settings.data_dir).resolve() / "ai-settings"
    account = "ai-settings-" + hashlib.sha256(str(root).encode()).hexdigest()
    try:
        return create_secret_store(settings.secret_backend, root), account
    except Exception:
        raise DomainError("本机凭据存储不可用，未读取或保存 AI 密钥。") from None


def resolve_ai_settings(settings: Settings) -> Settings:
    if settings.deployment_mode != "desktop":
        return settings
    store, account = settings_store(settings)
    return read_settings(settings, store, account)
