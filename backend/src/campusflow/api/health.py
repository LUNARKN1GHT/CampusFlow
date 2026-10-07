from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, text

router = APIRouter(tags=["system"])

DB_CONNECT_TIMEOUT_SECONDS = 3


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["campusflow-api"] = "campusflow-api"


class ReadyResponse(HealthResponse):
    database: Literal["ok"] = "ok"


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Report process liveness only, not database or model readiness."""
    return HealthResponse()


@router.get(
    "/ready",
    response_model=ReadyResponse,
    responses={503: {"description": "数据库不可用"}},
)
def ready(request: Request) -> ReadyResponse | JSONResponse:
    """Report database readiness via a short-timeout connection check.

    数据库不可达时返回 503，响应不包含连接串或口令；就绪同样不代表
    模型等尚未接入的能力可用。
    """
    settings = request.app.state.settings
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
