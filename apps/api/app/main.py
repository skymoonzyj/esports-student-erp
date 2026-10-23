from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import install_exception_handlers
from app.core.request_id import RequestIdMiddleware
from app.esports_assessments.http_support import PrivateEsportsAssessmentResponseMiddleware
from app.evaluations.routing import PrivateEvaluationResponseMiddleware
from app.food_preferences.routing import PrivateFoodPreferenceResponseMiddleware
from app.operations.metrics import RequestMetricsMiddleware, RequestMetricsStore
from app.operations.monitor import OperationsBackgroundMonitor
from app.operations.runtime import RuntimeSampler
from app.portal.routing import PrivatePortalResponseMiddleware
from app.registration.delivery import create_test_mailbox_sender
from app.registration.routing import PrivateRegistrationResponseMiddleware
from app.talent_tests.router import PrivateTalentResponseMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    resolved.validate_runtime_secrets()
    production = resolved.app_env == "production"

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        stop = asyncio.Event()
        task: asyncio.Task[None] | None = None
        if resolved.operations_monitor_enabled and resolved.app_env != "test":
            monitor = OperationsBackgroundMonitor(
                resolved,
                application.state.operations_request_metrics,
                application.state.operations_runtime_sampler,
            )
            task = asyncio.create_task(monitor.run(stop))
        try:
            yield
        finally:
            stop.set()
            if task is not None:
                await task

    app = FastAPI(
        title="Esports Education ERP",
        lifespan=lifespan,
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
    )
    app.state.settings = resolved
    request_metrics = RequestMetricsStore()
    app.state.operations_request_metrics = request_metrics
    app.state.operations_runtime_sampler = RuntimeSampler()
    registration_sender = create_test_mailbox_sender(
        app_env=resolved.app_env,
        server_host=resolved.registration_test_server_host,
        mailbox_dir=resolved.registration_test_mailbox_dir,
    )
    if registration_sender is not None:
        app.state.registration_verification_sender = registration_sender
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(PrivatePortalResponseMiddleware)
    app.add_middleware(PrivateEvaluationResponseMiddleware)
    app.add_middleware(PrivateFoodPreferenceResponseMiddleware)
    app.add_middleware(PrivateRegistrationResponseMiddleware)
    app.add_middleware(PrivateTalentResponseMiddleware)
    app.add_middleware(PrivateEsportsAssessmentResponseMiddleware)
    app.add_middleware(RequestMetricsMiddleware, store=request_metrics)
    app.include_router(api_router, prefix="/api/v1")
    install_exception_handlers(app)
    return app


app = create_app()
