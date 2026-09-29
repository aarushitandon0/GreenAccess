"""FastAPI application factory. Phase 0 exposes only `/api/health` (MASTERSPEC §12)."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.version import APP_VERSION, engine_versions

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="GreenAccess",
        version=APP_VERSION,
        description="Joint accessibility and carbon audit for a single URL.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.public_base_url, "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        """Liveness probe plus the pinned engine versions, for debugging demos."""
        return {
            "status": "ok",
            "version": APP_VERSION,
            "engines": engine_versions(),
        }

    return app


app = create_app()
