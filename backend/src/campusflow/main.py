from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from campusflow.api import courses as courses_api
from campusflow.api import schedule as schedule_api
from campusflow.api import tasks as tasks_api
from campusflow.api.health import router as health_router
from campusflow.core.config import Settings
from campusflow.domain.errors import DomainError, NotFoundError
from campusflow.infrastructure.db.engine import create_db_engine
from campusflow.infrastructure.db.session import create_session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    app = FastAPI(title=settings.app_name, version="0.1.0")

    engine = create_db_engine(settings)
    app.state.session_factory = create_session_factory(engine)

    for router in (health_router, courses_api.router, tasks_api.router, schedule_api.router):
        app.include_router(router, prefix="/api/v1")

    @app.exception_handler(NotFoundError)
    async def handle_not_found(request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return app


app = create_app()
