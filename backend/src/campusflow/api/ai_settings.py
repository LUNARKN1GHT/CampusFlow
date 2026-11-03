"""已登录桌面用户的 AI 配置与显式合成样本连接检查。"""

from io import BytesIO
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from PIL import Image, ImageDraw
from pydantic import BaseModel

from campusflow.application.ai_settings import AISettingsUpdate, public_settings, save_settings
from campusflow.application.ports.generation import GenerationRequest
from campusflow.domain.errors import DomainError, ModelOutputError, PermanentError, TransientError
from campusflow.infrastructure.ai_settings import resolve_ai_settings, settings_store
from campusflow.infrastructure.llm.vision import ZhipuVisionParser
from campusflow.workers.embeddings import build_embedder
from campusflow.workers.generation import build_text_generator


class PrivateValidationRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await original(request)
            except RequestValidationError:
                # FastAPI 默认校验响应可能包含 input，不能回显密钥原文。
                return JSONResponse(
                    status_code=422, content={"detail": "AI 设置格式无效，请检查型号与密钥格式。"}
                )

        return handler


router = APIRouter(prefix="/settings/ai", tags=["settings"], route_class=PrivateValidationRoute)


@router.get("")
def get_ai_settings(request: Request):
    return public_settings(resolve_ai_settings(request.app.state.settings))


@router.put("")
def put_ai_settings(payload: AISettingsUpdate, request: Request):
    base = request.app.state.settings
    store, account = settings_store(base)
    return public_settings(save_settings(base, store, account, payload))


class CheckRequest(BaseModel):
    capability: Literal["text", "vision", "embedding"]
    acknowledge_cost: Literal[True]


@router.post("/check")
def check_connection(payload: CheckRequest, request: Request):
    base = request.app.state.settings
    settings_store(base)  # 服务器部署不开放可消耗模型额度的管理检查。
    settings = resolve_ai_settings(base)
    if not public_settings(settings)["capabilities"][payload.capability]:
        raise DomainError("该能力尚未配置密钥，请先保存 AI 服务设置。")
    try:
        if payload.capability == "text":
            build_text_generator(settings).generate(
                GenerationRequest(
                    system_prompt="这是连接测试。只回答 OK。", source_json='{"sample":"synthetic"}'
                )
            )
        elif payload.capability == "embedding":
            build_embedder(settings).embed("合成测试：数据库课程周五交作业。")
        else:
            image = Image.new("RGB", (360, 80), "white")
            ImageDraw.Draw(image).text((12, 25), "SYNTHETIC TEST: homework Friday", fill="black")
            stream = BytesIO()
            image.save(stream, format="PNG")
            outcome = ZhipuVisionParser(
                settings.zhipu_api_key,
                model=settings.vision_model,
                timeout_seconds=settings.vision_timeout_seconds,
                max_retries=0,
            ).parse(stream.getvalue(), "image/png")
            if not outcome.fragments or outcome.failures:
                raise ModelOutputError("合成图片未完整识别")
    except Exception as error:
        # 只返回固定文案，不返回供应商正文、异常原文或凭据。
        code, message = "unavailable", "连接失败或等待超时，请检查网络后重试。"
        if isinstance(error, (PermanentError, TransientError)):
            if "401" in str(error) or "403" in str(error):
                code, message = "credentials", "密钥或账号权限被拒绝，请检查服务账号。"
            elif "429" in str(error):
                code, message = "quota", "服务限流或额度不足，请查看服务账户后重试。"
            elif isinstance(error, ModelOutputError):
                code, message = "invalid_output", "服务返回了响应，但未通过该能力的数据检查。"
            elif isinstance(error, PermanentError):
                code, message = "rejected", "服务拒绝请求，请检查型号和账号配置。"
        return {"ok": False, "capability": payload.capability, "code": code, "message": message}
    return {
        "ok": True,
        "capability": payload.capability,
        "code": "ok",
        "message": "该能力的合成样本检查通过；不代表所有资料都能正确处理。",
    }
