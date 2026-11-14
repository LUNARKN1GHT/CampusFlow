"""桌面模式 AI 服务配置（Issue #553）。

桌面安装包的用户拿不到 `ZHIPU_API_KEY`／`DEEPSEEK_API_KEY` 环境变量，界面又
提示"在设置中填写模型密钥"，因此这里提供可操作的入口：

- 模型密钥只写入本机凭据存储（P007 的 `SecretStore`，账户名固定为
  `ai-zhipu-api-key`／`ai-deepseek-api-key`），不写数据库、不进日志与响应；
- 文字供应方偏好不是密钥，写入 `<data_dir>/ai-settings.json`（0600，不含密钥）；
  文件损坏时回退默认 `zhipu` 并给出可见警告，不让启动失败；
- 启动时把已保存的密钥与供应方填进 `Settings`，环境变量优先：构造 `Settings`
  时已显式给出的字段（环境变量或调用方传参）不被存储值覆盖；
- 非桌面模式是 no-op：服务器／开发环境仍由环境变量配置，不读写数据目录。

本模块与 `application/bootstrap.py` 同属启动编排：装配基础设施适配器，业务
判断留在纯函数里；API 层只做 HTTP 校验与响应转换。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from campusflow.application.ports.secrets import SecretStore
from campusflow.core.config import Settings
from campusflow.domain.errors import DomainError
from campusflow.infrastructure.secrets import SecretBackendError, create_secret_store

logger = logging.getLogger(__name__)

AI_ZHIPU_API_KEY_ACCOUNT = "ai-zhipu-api-key"
AI_DEEPSEEK_API_KEY_ACCOUNT = "ai-deepseek-api-key"
AI_PREFERENCE_FILENAME = "ai-settings.json"
DEFAULT_TEXT_PROVIDER = "zhipu"
TEXT_PROVIDERS = ("zhipu", "deepseek")
# bootstrap（P007）记录的凭据后端就是实际写入的后端名，这里只读它来保持读写同一存储。
BOOTSTRAP_STATE_FILENAME = "state.json"

__all__ = [
    "AI_DEEPSEEK_API_KEY_ACCOUNT",
    "AI_PREFERENCE_FILENAME",
    "AI_ZHIPU_API_KEY_ACCOUNT",
    "AiSettingsStatus",
    "apply_stored_ai_settings",
    "build_status",
    "open_ai_secret_store",
    "read_text_provider_preference",
    "update_ai_settings",
]


@dataclass(frozen=True)
class AiSettingsStatus:
    """AI 服务配置状态；只描述可用性与存储后端，不含密钥明文或其片段。"""

    editable: bool
    text_provider: str
    zhipu_configured: bool
    deepseek_configured: bool
    text_available: bool
    vision_available: bool
    embedding_available: bool
    secret_backend: str
    restart_required: bool = False
    warnings: tuple[str, ...] = ()


def _desktop_root(settings: Settings) -> Path | None:
    """只有桌面模式且数据目录存在时才读写本机配置；其它情况返回 None。"""
    if settings.deployment_mode != "desktop" or not settings.data_dir:
        return None
    root = Path(settings.data_dir).expanduser()
    if not root.is_dir():
        return None
    return root


def _require_desktop_root(settings: Settings) -> Path:
    if settings.deployment_mode != "desktop":
        raise DomainError("仅桌面版支持在应用内保存模型密钥；当前由环境变量配置")
    if not settings.data_dir:
        raise DomainError("桌面模式缺少数据目录，无法保存模型密钥")
    return Path(settings.data_dir).expanduser()


def _bootstrap_secret_backend(root: Path) -> str | None:
    """读取 bootstrap 记录的后端名（如 `file`／`macos-keychain`）；读不到返回 None。"""
    try:
        raw = json.loads((root / BOOTSTRAP_STATE_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    backend = raw.get("secret_backend")
    return backend if isinstance(backend, str) and backend else None


def open_ai_secret_store(settings: Settings) -> SecretStore:
    """按 bootstrap 记录的后端创建凭据存储；不可用时抛 `SecretBackendError`。

    先尝试 state.json 记录的后端（与数据库口令同一存储），再尝试配置里的
    `CAMPUSFLOW_SECRET_BACKEND`；两者都不行时由调用方给出明确提示。
    """
    root = _require_desktop_root(settings)
    candidates: list[str] = []
    recorded = _bootstrap_secret_backend(root)
    if recorded:
        candidates.append(recorded)
    if settings.secret_backend not in candidates:
        candidates.append(settings.secret_backend)
    last_error: Exception | None = None
    for name in candidates:
        try:
            return create_secret_store(name, root)
        except SecretBackendError as exc:
            last_error = exc
    raise SecretBackendError("没有可用的本机凭据存储") from last_error


def read_text_provider_preference(settings: Settings) -> tuple[str | None, str | None]:
    """返回（已保存的供应方, 警告）；文件缺失返回 `(None, None)`，不视为错误。

    损坏或取值非法时保留警告并用 `None` 表示回退默认：调用方继续使用环境或
    默认配置，不因一个偏好文件让应用无法启动。
    """
    root = _desktop_root(settings)
    if root is None:
        return None, None
    path = root / AI_PREFERENCE_FILENAME
    if not path.is_file():
        return None, None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "已保存的 AI 供应方偏好无法读取，已回退默认 zhipu；可在设置页重新保存"
    if not isinstance(raw, dict):
        return None, "已保存的 AI 供应方偏好格式无效，已回退默认 zhipu；可在设置页重新保存"
    provider = raw.get("text_provider")
    if provider not in TEXT_PROVIDERS:
        return None, "已保存的 AI 供应方偏好取值无效，已回退默认 zhipu；可在设置页重新保存"
    return provider, None


def _write_text_provider_preference(root: Path, provider: str) -> None:
    """原子写入偏好文件并收紧到 0600；文件里只有供应方名称，不含任何密钥。"""
    path = root / AI_PREFERENCE_FILENAME
    handle, temp_name = tempfile.mkstemp(dir=root, prefix=".ai-settings-", suffix=".tmp")
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump({"text_provider": provider}, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def apply_stored_ai_settings(
    settings: Settings, *, secret_store: SecretStore | None = None
) -> list[str]:
    """启动时把已保存的密钥与供应方填进 Settings，返回给用户看的警告。

    环境变量优先：构造 `Settings` 时已显式给出的字段不会被存储值覆盖。
    非桌面模式、数据目录不存在或凭据存储不可用时只返回警告，绝不阻塞启动；
    警告文案固定，不包含密钥或异常原文。
    """
    root = _desktop_root(settings)
    if root is None:
        return []
    # 必须在赋值之前取快照：pydantic 的赋值也会把字段加入 fields_set。
    explicit = set(settings.model_fields_set)
    warnings: list[str] = []

    provider, preference_warning = read_text_provider_preference(settings)
    if preference_warning:
        warnings.append(preference_warning)
    if provider is not None and "text_provider" not in explicit:
        settings.text_provider = provider

    if secret_store is None:
        try:
            secret_store = open_ai_secret_store(settings)
        except Exception:
            warnings.append("本机凭据存储不可用，已保存的模型密钥没有加载")
            secret_store = None

    if secret_store is not None:
        for account, attribute in (
            (AI_ZHIPU_API_KEY_ACCOUNT, "zhipu_api_key"),
            (AI_DEEPSEEK_API_KEY_ACCOUNT, "deepseek_api_key"),
        ):
            if attribute in explicit:
                continue
            try:
                value = secret_store.get(account)
            except Exception:
                warnings.append("读取本机凭据存储失败，已保存的模型密钥没有加载")
                continue
            if value:
                setattr(settings, attribute, value)

    for warning in warnings:
        logger.warning("ai_settings_load_failed: %s", warning)
    return warnings


def _environment_overrides() -> list[str]:
    """列出当前进程里会影响 AI 配置的环境变量名（只返回名称，不返回取值）。"""
    names = []
    if os.environ.get("CAMPUSFLOW_TEXT_PROVIDER", "").strip():
        names.append("CAMPUSFLOW_TEXT_PROVIDER")
    if os.environ.get("ZHIPU_API_KEY", "").strip():
        names.append("ZHIPU_API_KEY")
    if os.environ.get("DEEPSEEK_API_KEY", "").strip():
        names.append("DEEPSEEK_API_KEY")
    return names


def build_status(
    settings: Settings,
    *,
    restart_required: bool = False,
    secret_store: SecretStore | None = None,
) -> AiSettingsStatus:
    """汇总配置状态；只返回布尔值与后端名称，不返回密钥或其片段。"""
    editable = settings.deployment_mode == "desktop"
    provider = (
        settings.text_provider
        if settings.text_provider in TEXT_PROVIDERS
        else DEFAULT_TEXT_PROVIDER
    )
    warnings: list[str] = []

    if editable:
        _stored_provider, preference_warning = read_text_provider_preference(settings)
        if preference_warning:
            warnings.append(preference_warning)
        if secret_store is not None:
            backend = secret_store.backend
        else:
            try:
                backend = open_ai_secret_store(settings).backend
            except Exception:
                backend = "unavailable"
                warnings.append("本机凭据存储不可用，无法保存或读取模型密钥")
    else:
        backend = "environment"
        if not settings.zhipu_api_key and not settings.deepseek_api_key:
            warnings.append(
                "当前由环境变量配置模型密钥；未检测到 ZHIPU_API_KEY 或 DEEPSEEK_API_KEY"
            )

    overrides = _environment_overrides()
    if editable and overrides:
        warnings.append(f"环境变量 {'、'.join(overrides)} 优先，保存的自定义值在重启后不会覆盖它")

    zhipu_configured = bool(settings.zhipu_api_key)
    deepseek_configured = bool(settings.deepseek_api_key)
    if provider == "zhipu":
        text_available = zhipu_configured
    elif provider == "deepseek":
        text_available = deepseek_configured
    else:
        text_available = False
    return AiSettingsStatus(
        editable=editable,
        text_provider=provider,
        zhipu_configured=zhipu_configured,
        deepseek_configured=deepseek_configured,
        text_available=text_available,
        # 图片识别与向量检索都走智谱适配器，只认 ZHIPU 密钥。
        vision_available=zhipu_configured,
        embedding_available=zhipu_configured,
        secret_backend=backend,
        restart_required=restart_required,
        warnings=tuple(warnings),
    )


def _clean_secret(label: str, value: str | None) -> str | None:
    """未提交（None）返回 None；提交空值或纯空白直接拒绝，避免把空密钥写进存储。"""
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        raise DomainError(f"{label}不能为空；如需删除已保存的密钥请勾选清除")
    return cleaned


def _next_secret(current: str | None, submitted: str | None, clear: bool) -> str | None:
    if submitted is not None:
        return submitted
    if clear:
        return None
    return current or None


def _store_secret(store: SecretStore, account: str, value: str | None, *, clear: bool) -> None:
    """写入或删除单个密钥并读回校验；失败时只抛固定中文说明，不带异常原文。"""
    try:
        if value is not None:
            store.set(account, value)
            if store.get(account) != value:
                raise DomainError("模型密钥写入后无法读回，请检查本机凭据存储后重试")
        elif clear:
            store.delete(account)
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError(
            f"无法访问本机凭据存储（{exc.__class__.__name__}），模型密钥没有保存"
        ) from None


def update_ai_settings(
    settings: Settings,
    *,
    text_provider: str | None = None,
    zhipu_api_key: str | None = None,
    deepseek_api_key: str | None = None,
    clear_zhipu_api_key: bool = False,
    clear_deepseek_api_key: bool = False,
    secret_store: SecretStore | None = None,
) -> AiSettingsStatus:
    """校验并保存 AI 配置，返回保存后的状态（`restart_required=True`）。

    `None` 表示调用方没有提交该字段；空字符串表示提交了空值（拒绝）。任一校验
    失败都在写存储之前抛 `DomainError`，不会留下半套配置。
    """
    root = _require_desktop_root(settings)
    if zhipu_api_key is not None and clear_zhipu_api_key:
        raise DomainError("同一个请求不能既填写又清除 ZHIPU API 密钥")
    if deepseek_api_key is not None and clear_deepseek_api_key:
        raise DomainError("同一个请求不能既填写又清除 DeepSeek API 密钥")

    new_zhipu = _clean_secret("ZHIPU API 密钥", zhipu_api_key)
    new_deepseek = _clean_secret("DeepSeek API 密钥", deepseek_api_key)
    final_provider = text_provider if text_provider is not None else settings.text_provider
    if final_provider not in TEXT_PROVIDERS:
        raise DomainError(f"未知的文字模型供应方：{final_provider}")

    final_zhipu = _next_secret(settings.zhipu_api_key, new_zhipu, clear_zhipu_api_key)
    final_deepseek = _next_secret(settings.deepseek_api_key, new_deepseek, clear_deepseek_api_key)
    if final_provider == "zhipu" and not final_zhipu:
        raise DomainError(
            "文字供应方为 ZHIPU 时必须配置 ZHIPU API 密钥；请填写密钥或切换到 DeepSeek"
        )
    if final_provider == "deepseek" and not final_deepseek:
        raise DomainError(
            "文字供应方为 DeepSeek 时必须配置 DeepSeek API 密钥；请填写密钥或切换回 ZHIPU"
        )

    if secret_store is None:
        try:
            secret_store = open_ai_secret_store(settings)
        except Exception as exc:
            raise DomainError(
                f"本机凭据存储不可用（{exc.__class__.__name__}），模型密钥没有保存"
            ) from None

    _store_secret(
        secret_store,
        AI_ZHIPU_API_KEY_ACCOUNT,
        new_zhipu,
        clear=clear_zhipu_api_key,
    )
    _store_secret(
        secret_store,
        AI_DEEPSEEK_API_KEY_ACCOUNT,
        new_deepseek,
        clear=clear_deepseek_api_key,
    )
    if text_provider is not None:
        try:
            _write_text_provider_preference(root, final_provider)
        except OSError as exc:
            raise DomainError(
                f"无法写入 AI 供应方偏好文件（{exc.__class__.__name__}），请检查数据目录权限"
            ) from None

    settings.text_provider = final_provider
    settings.zhipu_api_key = final_zhipu
    settings.deepseek_api_key = final_deepseek
    # 后台 Worker 是独立进程，保存后需要重启才会使用新配置；API 进程内立即生效。
    return build_status(settings, restart_required=True, secret_store=secret_store)
