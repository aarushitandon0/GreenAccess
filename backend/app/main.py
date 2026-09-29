"""FastAPI application factory (MASTERSPEC §12).

Single responsibility: assemble the app — middleware, error handlers, routes —
and own the lifespan of the objects the routes share (database, event hub, job
runner, rate limiter).

``create_app`` takes optional overrides so tests can swap the scanner pipeline
for a stub and the redirect pre-flight for an offline one, without patching.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import install_error_handlers
from app.api.events import EventHub
from app.api.ratelimit import SlidingWindowLimiter
from app.api.runner import JobFactory, ScanRunner, pipeline_job
from app.api.scans import router as scans_router
from app.api.state import AppState, Preflight
from app.config import Settings, get_settings
from app.db.repository import ScanRepository
from app.models import ApiError, ErrorCode
from app.security.redirects import preflight_redirects
from app.security.ssrf import ValidatedUrl
from app.version import APP_VERSION, engine_versions

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

#: The Vite dev server, under both names a developer might open it by.
DEV_ORIGINS: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")


async def _default_preflight(url: str, allowed: tuple[str, ...]) -> ValidatedUrl:
    return await preflight_redirects(url, allowed_local_hosts=allowed)


def create_app(
    settings: Settings | None = None,
    *,
    job_factory: JobFactory = pipeline_job,
    preflight: Preflight = _default_preflight,
    limiter: SlidingWindowLimiter | None = None,
) -> FastAPI:
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        repository = ScanRepository(resolved.database_url)
        await asyncio.to_thread(repository.create_all)
        interrupted = await repository.fail_unfinished(
            ApiError(
                code=ErrorCode.CANCELLED,
                message="the server restarted before this scan finished",
            )
        )
        if interrupted:
            logger.warning("marked %d interrupted scan(s) as failed", interrupted)

        hub = EventHub()
        runner = ScanRunner(
            settings=resolved, repository=repository, hub=hub, job_factory=job_factory
        )
        app.state.greenaccess = AppState(
            settings=resolved,
            repository=repository,
            hub=hub,
            runner=runner,
            limiter=limiter or SlidingWindowLimiter(),
            preflight=preflight,
        )
        try:
            yield
        finally:
            await runner.shutdown()
            repository.dispose()

    app = FastAPI(
        title="GreenAccess",
        version=APP_VERSION,
        description=(
            "Joint accessibility and carbon audit for a single URL. Accessibility "
            "results are automated checks only; carbon figures are estimates from "
            "the Sustainable Web Design model."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(dict.fromkeys((resolved.public_base_url, *DEV_ORIGINS))),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Last-Event-ID"],
        expose_headers=["Location", "Retry-After"],
    )
    install_error_handlers(app)
    app.include_router(scans_router)

    @app.get("/api/health", tags=["meta"])
    async def health() -> dict[str, object]:
        """Liveness probe plus the pinned engine versions, for debugging demos."""
        return {
            "status": "ok",
            "version": APP_VERSION,
            "engines": engine_versions(),
        }

    return app


app = create_app()
