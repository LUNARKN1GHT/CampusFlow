from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["campusflow-api"] = "campusflow-api"


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Report process liveness only, not database or model readiness."""
    return HealthResponse()
