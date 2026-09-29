# GreenAccess — developer entry points.
#
# Recipes stay to one command per line and delegate anything that needs process
# orchestration to a Python helper, so the same Makefile works whether make
# invokes cmd.exe (Windows) or sh (Linux, macOS, Docker).

ifeq ($(OS),Windows_NT)
  PY := backend/.venv/Scripts/python.exe
  NPM := npm.cmd
else
  PY := backend/.venv/bin/python
  NPM := npm
endif

# Default target for `make scan`. Override with: make scan URL=https://example.com
URL ?= http://localhost:8081

.PHONY: help install dev demo test test-backend test-frontend lint lint-backend \
        lint-frontend contrast assets weight scan types e2e dogfood build clean

help:
	@echo "GreenAccess targets:"
	@echo "  install    install backend and frontend dependencies"
	@echo "  dev        run backend :8000, frontend :5173, demo :8081, trackers :8082"
	@echo "  demo       run only the demo site and its tracker host"
	@echo "  test       backend pytest + frontend vitest"
	@echo "  lint       ruff + contrast check + tsc + eslint"
	@echo "  assets     regenerate the Daily Herald media assets"
	@echo "  weight     measure the demo site's first-load transfer size"
	@echo "  scan       run the scanner CLI against URL=<url>"
	@echo "  types      regenerate frontend/src/lib/types.ts from backend/app/models.py"
	@echo "  e2e        Playwright end-to-end run against the demo site"
	@echo "  dogfood    scan GreenAccess's own frontend"

# ---------------------------------------------------------------- setup ----

install:
	uv venv --python 3.12 backend/.venv
	uv pip install --python backend/.venv -r backend/requirements.lock.txt
	cd frontend && $(NPM) install

# ------------------------------------------------------------------ run ----

dev:
	$(PY) scripts/dev.py

demo:
	$(PY) scripts/dev.py --only demo

# ----------------------------------------------------------------- test ----

test: test-backend test-frontend

test-backend:
	cd backend && ../$(PY) -m pytest -q

test-frontend:
	cd frontend && $(NPM) run test

# ----------------------------------------------------------------- lint ----

lint: lint-backend contrast lint-frontend

# Covers the backend package, the developer scripts and the demo server, all
# against the one ruff config in backend/pyproject.toml.
lint-backend:
	$(PY) -m ruff check --config backend/pyproject.toml backend scripts demo-site/server.py
	$(PY) -m ruff format --check --config backend/pyproject.toml backend scripts demo-site/server.py

# MASTERSPEC §13 requires every token pair to meet WCAG AA. This is the gate.
contrast:
	$(PY) scripts/contrast_check.py

lint-frontend:
	cd frontend && $(NPM) run typecheck
	cd frontend && $(NPM) run lint

# ----------------------------------------------------------------- demo ----

assets:
	$(PY) scripts/make_demo_assets.py

weight:
	$(PY) scripts/demo_page_weight.py

# ---------------------------------------------------------------- scan -----
# Scans URL=<url> and prints steps, scores and trade-offs. The demo site lives
# on localhost, which the SSRF guard blocks by default, so the two demo hosts
# are allow-listed explicitly rather than by disabling the guard.

scan:
	cd backend && ../$(PY) -m app.cli scan $(URL) --allow-local localhost:8081 --allow-local localhost:8082

# ---------------------------------------------------------------- types ----
# The frontend's API types are generated from the pydantic models; a backend
# test fails if the committed file is stale.

types:
	$(PY) scripts/gen_ts_types.py

e2e:
	@echo "make e2e: not implemented yet (frontend phase)"

dogfood:
	@echo "make dogfood: not implemented yet (self-audit phase)"

# ---------------------------------------------------------------- build ----

build:
	cd frontend && $(NPM) run build

clean:
	$(PY) scripts/clean.py
