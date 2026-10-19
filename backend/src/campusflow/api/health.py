"""存活与就绪检查。

存活只代表 API 进程可响应；就绪额外验证数据库连接。两者都不代表
模型、队列或外部日历等依赖已经可用，不能用健康状态代替业务结果。
"""

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, text

from campusflow.core.config import DB_CONNECT_TIMEOUT_SECONDS

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["campusflow-api"] = "campusflow-api"


class ReadyResponse(HealthResponse):
    database: Literal["ok"] = "ok"


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """仅报告进程存活，不检查数据库、模型或外部同步状态。"""
    return HealthResponse()


@router.get(
    "/ready",
    response_model=ReadyResponse,
    responses={503: {"description": "数据库不可用"}},
)
def ready(request: Request) -> ReadyResponse | JSONResponse:
    """用短超时连接检查数据库；不可达时返回 503。

    响应只给固定中文文案，不包含连接串、主机名或异常原文。数据库就绪也
    不代表模型、队列等尚未接入的能力可用。
    """
    settings = request.app.state.settings
    # 每次请求单独建引擎并设置连接超时，避免就绪探针被数据库卡住。
    engine = create_engine(
        settings.database_url,
        connect_args={"connect_timeout": DB_CONNECT_TIMEOUT_SECONDS},
    )
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"detail": "数据库不可用"})
    finally:
        engine.dispose()
    return ReadyResponse()
