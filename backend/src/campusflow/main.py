from fastapi import FastAPI

from campusflow.api.health import router as health_router
from campusflow.core.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.include_router(health_router, prefix="/api/v1")
    return app


app = create_app()
