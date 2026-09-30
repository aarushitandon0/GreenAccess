# GreenAccess, production image: the API and the built UI on one origin.
#
# Development uses backend/Dockerfile and frontend/Dockerfile as two containers
# (see docker-compose.yml). This is the deployment build, and it is one
# container on purpose: frontend/src/lib/api.ts calls the API at the relative
# base "/api", so putting both behind one origin keeps that base true with no
# CORS pre-flight on every call, no second certificate, and an SSE stream that
# no cross-origin proxy can buffer.
#
# Build from the repository root:
#   docker build -t greenaccess .
#   docker run --rm -p 8000:8000 -v greenaccess-data:/app/data greenaccess

# --------------------------------------------------------------------------- #
# Stage 1: build the frontend. Nothing from this stage ships except dist/.
# --------------------------------------------------------------------------- #
FROM node:24-alpine AS frontend

WORKDIR /build

# Dependencies first, so this layer caches independently of the source.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build

# --------------------------------------------------------------------------- #
# Stage 2: the runtime.
#
# The base tag is pinned in lockstep with `playwright==1.63.0` in
# backend/pyproject.toml. The Playwright Python library and the browsers in the
# image must match exactly or Playwright refuses to launch. Ubuntu Noble
# (24.04) provides the Python 3.12 that CLAUDE.md pins.
# --------------------------------------------------------------------------- #
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Serve the UI built in stage 1. Explicit: app/api/spa.py mounts nothing
    # without this, so a stale build can never be served by accident.
    GREENACCESS_STATIC_DIR=/app/static \
    # Everything the app writes lives under /app/data: the database named here
    # plus the screenshots, patched copies and zips that config.py puts beside
    # it by default. That one directory is the only mount a deployment needs.
    DATABASE_URL=sqlite:////app/data/greenaccess.db \
    # A public deployment must not be able to spend money on Anthropic calls
    # unless it is turned on deliberately. Overridden by a platform secret.
    LLM_OFFLINE=1

WORKDIR /app

COPY backend/requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt

# The base image already ships the browsers. This is a no-op that fails loudly
# if the pinned library and the image ever drift apart.
RUN python -m playwright install --with-deps chromium

COPY backend/pyproject.toml ./
COPY backend/app ./app
COPY --from=frontend /build/dist ./static

# The scan browser renders untrusted pages, so it does not run as root. The
# base image ships a `pwuser`; the data directory is the only writable path.
RUN mkdir -p /app/data && chown -R pwuser:pwuser /app/data /app/static
USER pwuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status==200 else 1)"

# One worker deliberately. Scans are capped by MAX_CONCURRENT_SCANS inside the
# process, the SSE hub holds each scan's subscribers in memory, and SQLite has
# one writer: a second worker would break all three.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
