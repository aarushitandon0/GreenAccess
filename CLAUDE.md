# CLAUDE.md — GreenAccess

GreenAccess audits a website for **accessibility and carbon footprint together**, explains where the fixes help both or conflict, generates fixes with an LLM, applies them, and **re-scans to show real before/after numbers**.

The full product definition lives in `MASTERSPEC.md`. **Read the relevant section of it before starting any task.** If this file and the spec disagree, stop and ask.

## Working agreement

1. **Plan first.** For any task touching more than 2 files, write a short plan (files, approach, tests) and wait for confirmation unless the prompt says "proceed".
2. **One phase at a time.** Do only what the current phase prompt asks. Do not start features from later phases, even if they look easy.
3. **Small, verified commits.** After each logical unit: run tests + lint, then commit with a conventional message (`feat(scanner): ...`). Never commit failing tests.
4. **Never fake results.** No hardcoded scores, no mock numbers presented as real. Fixtures are allowed only in `tests/` and `backend/app/fixtures/` (for offline demo fallback) and must be labelled as cached.
5. **Ask before changing** the spec, scoring formulas, data models, or adding a dependency not listed in the spec.
6. **Report honestly.** End every task with: what was done, what was verified (with command output), what is not done or uncertain.

## Stack (pinned in lockfiles; do not upgrade casually)

- Backend: Python 3.12, FastAPI, Uvicorn, Playwright (async), axe-core (vendored npm file, version pinned), lxml, Pillow, SQLModel/SQLite, httpx, pydantic v2, pytest, ruff
- Frontend: React 18 + TypeScript + Vite, plain CSS with design tokens (no UI framework), vitest, Playwright for e2e
- LLM: Anthropic API via the official SDK, model name from env `ANTHROPIC_MODEL`
- Run everything in Docker (official Playwright image, pinned tag). Local dev must also work with `make dev`.

## Repo layout

```
backend/app/{api,scanner,carbon,scoring,tradeoffs,llm,patcher,db,security,fixtures}
backend/tests/
frontend/src/{pages,components,lib,styles}
demo-site/            # deliberately bad "Daily Herald" site; see MASTERSPEC §11
scripts/
docs/
MASTERSPEC.md  CLAUDE.md  Makefile  docker-compose.yml
```

## Commands (keep this list current)

- `make dev` — backend, frontend, demo site
- `make test` — backend pytest + frontend vitest
- `make lint` — ruff + tsc --noEmit + eslint
- `make scan URL=http://localhost:8081` — run scanner CLI, print steps, scores and trade-offs
- `make types` — regenerate `frontend/src/lib/types.ts` from `backend/app/models.py` (a test fails if stale)
- `make demo-record` — full fix loop on the demo with the live LLM; records the LLM cache and demo fixtures
- `make demo-replay` — the same loop with `LLM_OFFLINE=1`, from the committed cache
- `make e2e` — Playwright end-to-end against the demo site
- `make dogfood` — scan GreenAccess's own frontend; must score 100 accessibility

## Code conventions

- Python: type hints everywhere, pydantic models for all cross-module data, async for I/O, no bare `except`, no `print` (use `logging`).
- TypeScript: `strict`, no `any`, API types generated or mirrored from `backend/app/models.py` in `frontend/src/lib/types.ts`.
- Every module has a docstring stating its single responsibility.
- Functions that compute scores/carbon are **pure** and unit-tested with table-driven tests.
- Constants (weights, bands, carbon factors) live in one file each, with a source comment and pinned version.

## Security rules (non-negotiable)

- All user-supplied URLs go through `security/ssrf.py` before any browser or HTTP call. Block private, loopback, link-local, metadata (`169.254.169.254`), non-http(s) schemes, and re-check after redirects and DNS resolution.
- Dev/demo hosts are allowed only via env `ALLOWED_LOCAL_HOSTS` (explicit list), never by disabling the guard.
- Scans run with: navigation timeout 30s, total scan timeout 90s, max 2 concurrent scans, page-weight abort at 25 MB, fresh browser context per scan.
- Never send full page HTML to the LLM. Send only the offending element and limited parent context.
- API keys only via env. Never log keys or full page contents.
- Patched output is served as static files with a restrictive CSP; never executed server-side.

## Accuracy and wording rules

- Say "automated checks" or "issues detected". **Never** say "WCAG compliant" or "accessible" as a guarantee.
- Carbon values are **estimates** from the Sustainable Web Design model; label them so in UI and API.
- Keyboard traps are detected by our own Tab-crawl, not axe.
- AI output is labelled "AI-generated, review before use". Decorative images get `alt=""`.
- The score after fixes must come from a real re-scan of the patched page.

## Definition of done (any task)

- Tests written and passing; lint clean
- Works through the UI or CLI as described in the phase acceptance criteria
- No new console errors or accessibility violations in GreenAccess's own UI
- Docs/Makefile updated if commands or env vars changed
- Commit made

## When stuck

Prefer the simplest thing that meets the acceptance criteria. If a site cannot be patched reliably, report it as "manual fix needed" rather than guessing. If time is short, follow the cut order in `MASTERSPEC.md §14`.