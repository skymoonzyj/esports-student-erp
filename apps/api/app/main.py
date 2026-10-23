from __future__ import annotations

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import install_exception_handlers
from app.core.request_id import RequestIdMiddleware
from app.evaluations.routing import PrivateEvaluationResponseMiddleware
from app.portal.routing import PrivatePortalResponseMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    resolved.validate_runtime_secrets()
    production = resolved.app_env == "production"
    app = FastAPI(
        title="Esports Education ERP",
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
    )
    app.state.settings = resolved
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(PrivatePortalResponseMiddleware)
    app.add_middleware(PrivateEvaluationResponseMiddleware)
    app.include_router(api_router, prefix="/api/v1")
    install_exception_handlers(app)
    return app


app = create_app()
