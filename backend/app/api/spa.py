"""Serve the built frontend from the API process, for production deployments.

Single responsibility: hand out the files in ``frontend/dist`` and fall back to
``index.html`` so a deep link still loads the app.

Why the backend serves the UI at all. ``frontend/src/lib/api.ts`` calls the API
at the relative base ``/api``. In development Vite proxies that to port 8000.
In production the simplest way to keep that relative base true is to put both
on one origin, which also means no CORS pre-flight on every call, no second
certificate, and an SSE stream that no cross-origin proxy can buffer.

This is opt-in and explicit. The mount only exists when
``GREENACCESS_STATIC_DIR`` names a built frontend, which the deployment image
sets and a developer machine does not, so local development, the test suite and
the CLI are all untouched by it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.types import Scope

logger = logging.getLogger(__name__)

__all__ = ["mount_frontend"]

#: Prefixes the API owns. A request under one of these must never be answered
#: with index.html, or a mistyped endpoint would return an HTML page with a
#: 200 and the client would parse it as JSON.
_API_PREFIXES: Final[tuple[str, ...]] = ("/api", "/patched", "/docs", "/redoc", "/openapi.json")

#: Vite writes content-hashed filenames into this directory, so its contents
#: are immutable and can be cached hard. index.html must not be.
_HASHED_DIR: Final[str] = "assets"


def _is_api_path(scope: Scope) -> bool:
    """Whether this request is for something the API owns rather than the app.

    Read off the scope rather than the path StaticFiles was handed, because
    that one has been made relative to the mount and lost its leading slash.
    """
    raw = str(scope.get("path", ""))
    if not raw.startswith("/"):
        raw = "/" + raw
    return raw.startswith(_API_PREFIXES)


class _SpaFiles(StaticFiles):
    """StaticFiles that answers an unknown path with index.html.

    The app is a single page: every chapter is a fragment of one document, so
    there are no server-side routes to match. Anything that is not a real file
    is still the app.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        # StaticFiles signals a miss by raising, not by returning a 404, so the
        # fallback has to be in an except block rather than a status check.
        try:
            response = await super().get_response(path, scope)
        except HTTPException as missing:
            if missing.status_code != 404 or _is_api_path(scope):
                raise
            return await super().get_response("index.html", scope)
        if response.status_code == 404 and not _is_api_path(scope):
            return await super().get_response("index.html", scope)
        return response

    def file_response(self, full_path: str, *args: object, **kwargs: object) -> Response:
        response = super().file_response(full_path, *args, **kwargs)  # type: ignore[arg-type]
        if isinstance(response, FileResponse):
            hashed = f"/{_HASHED_DIR}/" in full_path.replace("\\", "/")
            response.headers["Cache-Control"] = (
                "public, max-age=31536000, immutable" if hashed else "no-cache"
            )
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response


def resolve_static_dir(configured: Path | None) -> Path | None:
    """The directory holding a built frontend, or None if there is not one.

    Explicit only: nothing is served unless ``GREENACCESS_STATIC_DIR`` names a
    directory with an ``index.html`` in it. Picking up ``frontend/dist``
    automatically would be worse than useless in development, where that
    directory is a stale leftover of the last ``npm run build`` and the live
    UI is the Vite dev server on another port. The deployment image sets the
    variable; a developer's machine never has it set.

    A configured path that is not usable is a misconfiguration worth saying out
    loud, rather than silently serving nothing.
    """
    if configured is None:
        return None
    if (configured / "index.html").is_file():
        return configured
    logger.warning("GREENACCESS_STATIC_DIR=%s has no index.html; not serving a UI", configured)
    return None


def mount_frontend(app: FastAPI, static_dir: Path | None) -> bool:
    """Mount the built frontend at ``/``. Returns whether anything was mounted.

    Must be called after every router is registered: the mount is at the root
    and would otherwise shadow them.
    """
    resolved = resolve_static_dir(static_dir)
    if resolved is None:
        return False
    app.mount("/", _SpaFiles(directory=resolved, html=True), name="frontend")
    logger.info("serving the built frontend from %s", resolved)
    return True
