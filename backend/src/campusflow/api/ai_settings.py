"""AI 服务配置 HTTP 路由（Issue #553）。

GET 返回可用性与存储后端，PUT 只在桌面模式允许保存模型密钥。响应、错误文案
与日志都不包含密钥明文或其片段；文字键用于文字／视觉／向量，DeepSeek 键只用于
文字生成。保存后 API 进程内立即生效，后台 Worker 需要重启。
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, SecretStr

from campusflow.application import ai_settings as use_cases
from campusflow.application.ai_settings import AiSettingsStatus
from campusflow.application.ports.secrets import SecretStore

router = APIRouter(prefix="/ai-settings", tags=["settings"])


class AiSettingsUpdate(BaseModel):
    """PUT 请求体；用 `model_fields_set` 区分"未提交"与"提交了空值"。

    密钥字段使用 `SecretStr`：字符串密钥不会进入模型 repr；只有把密钥塞进
    非字符串结构时，FastAPI 通用 422 才会回显该字段原文。
    """

    model_config = ConfigDict(extra="forbid")

    text_provider: Literal["zhipu", "deepseek"] | None = None
    zhipu_api_key: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None
    clear_zhipu_api_key: bool = False
    clear_deepseek_api_key: bool = False


class AiSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    editable: bool
    text_provider: str
    zhipu_configured: bool
    deepseek_configured: bool
    text_available: bool
    vision_available: bool
    embedding_available: bool
    secret_backend: str
    restart_required: bool
    warnings: list[str]


def _secret_value(payload: AiSettingsUpdate, field: str) -> str | None:
    """只返回真正提交过的密钥取值；未提交返回 None，不读默认值。"""
    if field not in payload.model_fields_set:
        return None
    value: SecretStr | None = getattr(payload, field)
    return None if value is None else value.get_secret_value()


def get_ai_secret_store(request: Request) -> SecretStore | None:
    """按应用状态缓存凭据存储；不可用或非桌面模式返回 None（不抛错，交给用例提示）。"""
    cached = getattr(request.app.state, "ai_secret_store", None)
    if cached is not None:
        return cached
    settings = request.app.state.settings
    if settings.deployment_mode != "desktop":
        return None
    try:
        store = use_cases.open_ai_secret_store(settings)
    except Exception:  # 后端不可用时由状态里的警告说明，不让读取接口 500
        return None
    request.app.state.ai_secret_store = store
    return store


def _to_out(status: AiSettingsStatus) -> AiSettingsOut:
    return AiSettingsOut.model_validate(status)


@router.get("", response_model=AiSettingsOut)
def read_ai_settings(
    request: Request, store: Annotated[SecretStore | None, Depends(get_ai_secret_store)]
) -> AiSettingsOut:
    """读取配置状态；非桌面模式返回环境变量配置的只读视图。"""
    return _to_out(
        use_cases.build_status(
            request.app.state.settings,
            restart_required=bool(getattr(request.app.state, "ai_restart_required", False)),
            secret_store=store,
        )
    )


@router.put("", response_model=AiSettingsOut)
def update_ai_settings(
    payload: AiSettingsUpdate,
    request: Request,
    store: Annotated[SecretStore | None, Depends(get_ai_secret_store)],
) -> AiSettingsOut:
    """保存模型密钥与文字供应方；仅桌面模式可编辑，服务器模式返回 403。"""
    settings = request.app.state.settings
    if settings.deployment_mode != "desktop":
        raise HTTPException(
            status_code=403,
            detail="仅桌面版支持在应用内保存模型密钥；服务器模式由环境变量配置",
        )
    status = use_cases.update_ai_settings(
        settings,
        text_provider=payload.text_provider
        if "text_provider" in payload.model_fields_set
        else None,
        zhipu_api_key=_secret_value(payload, "zhipu_api_key"),
        deepseek_api_key=_secret_value(payload, "deepseek_api_key"),
        clear_zhipu_api_key=payload.clear_zhipu_api_key,
        clear_deepseek_api_key=payload.clear_deepseek_api_key,
        secret_store=store,
    )
    # 状态接口据此持续提示"后台资料处理需重启应用生效"，直到进程重启。
    request.app.state.ai_restart_required = True
    return _to_out(status)
