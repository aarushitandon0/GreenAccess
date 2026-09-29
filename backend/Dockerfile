# The base image tag is pinned in lockstep with `playwright==1.63.0` in
# pyproject.toml. The Playwright Python library and the browsers shipped in the
# image must match exactly, or Playwright refuses to launch.
#
#   pyproject.toml : playwright==1.63.0
#   this image     : mcr.microsoft.com/playwright/python:v1.63.0-noble
#
# Ubuntu Noble (24.04) provides Python 3.12, which is the version CLAUDE.md pins.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first so the layer caches independently of application source.
COPY requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt

# The base image already ships the browsers; this is a no-op that fails loudly
# if the pinned library and the image ever drift apart.
RUN python -m playwright install --with-deps chromium

COPY pyproject.toml ./
COPY app ./app
COPY tests ./tests

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
