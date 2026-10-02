"""Serve the recorded demo instead of scanning, when ``DEMO_FALLBACK=1``.

Single responsibility: answer the scan half of the API from
``app/fixtures/demo_scan_{before,after}.json`` so the product can be shown
where a real scan cannot run -- a host with no Chromium, no outbound network,
or too little memory for a browser.

The routes here mirror the shapes in :mod:`app.api.scans` and
:mod:`app.api.fixes` exactly, so the frontend needs no knowledge of this mode.
This router is registered *before* those two, and FastAPI resolves by first
match, so these win while the mode is on.

What it deliberately does NOT do (CLAUDE.md rule 4, "never fake results"):

* It refuses any URL but the recorded one. Returning the Daily Herald's numbers
  for someone else's site would be presenting cached data as their result, so a
  different URL is an explicit, readable refusal instead.
* Every scan it returns carries ``_cached`` metadata naming the recording date,
  and ``GET /api/demo`` reports ``cached=True``, so a caller cannot mistake this
  for a live run without ignoring the payload.

The numbers themselves are real: a recorded scan of the real demo site, not
invented values.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from sse_starlette import EventSourceResponse, ServerSentEvent

from app.api.errors import ApiException
from app.api.state import AppState, get_state
from app.fixtures.loader import DEMO_SCAN_ID, RecordedDemo, load_demo
from app.models import (
    DemoInfo,
    DoneEvent,
    ErrorCode,
    FixesResponse,
    HistoryEntry,
    HistoryResponse,
    PatchAccepted,
    Scan,
    ScanCreated,
    ScanRequest,
    ScanStatus,
    StepEvent,
    StepStatus,
)

logger = logging.getLogger(__name__)

__all__ = ["router"]

router = APIRouter(prefix="/api", tags=["demo-fallback"])

State = Annotated[AppState, Depends(get_state)]

#: Pause between replayed steps, so the UI's progress list is readable rather
#: than arriving in one frame. Short enough not to make the demo feel slow.
STEP_DELAY_S: Final = 0.35

#: The steps the real pipeline emits, in order, for each phase. Replaying the
#: real step names keeps the UI honest about what a live run would do.
_SCAN_STEPS: Final = (
    ("validate", "Checking the URL"),
    ("load", "Loading the page"),
    ("a11y", "Running automated accessibility checks"),
    ("keyboard", "Crawling with the keyboard"),
    ("aria", "Reading the accessibility tree"),
    ("carbon", "Estimating transfer and carbon"),
    ("green", "Checking green hosting"),
    ("score", "Scoring"),
    ("tradeoffs", "Finding trade-offs and synergies"),
    ("persist", "Saving the result"),
)

_PATCH_STEPS: Final = (
    ("fixes", "Reading the recorded fixes"),
    ("patch", "Applying them to a copy"),
    ("rescan", "Re-scanning the patched copy"),
)


def _demo() -> RecordedDemo:
    try:
        return load_demo()
    except (FileNotFoundError, ValueError) as exc:
        # Misconfiguration, not a client error: the mode is on but unusable.
        logger.error("DEMO_FALLBACK is on but the recording is unusable: %s", exc)
        raise ApiException(
            ErrorCode.NOT_FOUND,
            "this deployment is in cached-demo mode, but its recording is missing",
        ) from exc


def _as_payload(scan: Scan, demo: RecordedDemo) -> dict[str, Any]:
    """The scan as JSON, with the cached-data label attached."""
    payload: dict[str, Any] = scan.model_dump(mode="json")
    payload["_cached"] = {
        "label": demo.label,
        "recorded_at": demo.recorded_at,
        "live": False,
    }
    return payload


def _require_known_url(submitted: str, demo: RecordedDemo) -> None:
    """Refuse to answer for a site this recording is not of."""
    recorded = demo.before.url
    if submitted.rstrip("/") == recorded.rstrip("/"):
        return
    raise ApiException(
        ErrorCode.URL_BLOCKED,
        (
            "this deployment runs from a cached recording and cannot scan new "
            f"sites. It can only replay the recorded run of {recorded}. "
            "Run GreenAccess locally to scan your own URL."
        ),
    )


# --------------------------------------------------------------------------
# Scan routes
# --------------------------------------------------------------------------


@router.get("/demo", response_model=None)
async def get_demo(state: State) -> JSONResponse:
    """Where the demo lives, plus the fact that this deployment is cached."""
    demo = _demo()
    info = DemoInfo(url=demo.before.url)
    body = info.model_dump(mode="json")
    body["cached"] = True
    body["label"] = demo.label
    body["recorded_at"] = demo.recorded_at
    return JSONResponse(body)


@router.post("/scans", status_code=202, response_model=None)
async def create_scan(request: ScanRequest, response: Response) -> ScanCreated:
    """Accept only the recorded URL, and hand back the recording's id."""
    demo = _demo()
    _require_known_url(str(request.url), demo)
    response.headers["Location"] = f"/api/scans/{DEMO_SCAN_ID}"
    return ScanCreated(scan_id=DEMO_SCAN_ID)


@router.get("/scans/{scan_id}", response_model=None)
async def get_scan(scan_id: str, request: Request) -> JSONResponse:
    """The recorded scan.

    Which chapter depends on whether the fix loop has been asked for in this
    browser: the UI calls ``POST /patch`` first and then re-reads the scan, so
    the ``patched`` query the frontend sends decides. Without it, the first
    chapter is returned, which is what a fresh visitor should see.
    """
    demo = _demo()
    if scan_id != DEMO_SCAN_ID:
        raise ApiException(ErrorCode.NOT_FOUND, f"no scan with id {scan_id!r}")
    patched = request.query_params.get("patched") == "1"
    scan = demo.after if patched else demo.before
    return JSONResponse(_as_payload(scan, demo))


@router.post("/scans/{scan_id}/cancel", response_model=None)
async def cancel_scan(scan_id: str) -> JSONResponse:
    """Idempotent, and a recording is always already finished."""
    demo = _demo()
    if scan_id != DEMO_SCAN_ID:
        raise ApiException(ErrorCode.NOT_FOUND, f"no scan with id {scan_id!r}")
    return JSONResponse(_as_payload(demo.before, demo))


async def _replay(
    steps: tuple[tuple[str, str], ...], start_id: int
) -> AsyncIterator[ServerSentEvent]:
    """Yield the recorded phase's steps, then one `done`.

    ``ms`` is 0 on every step: the recording does not store per-step timings,
    and inventing a duration would be inventing a measurement (CLAUDE.md rule
    4). The scores that follow are all real, recorded values.
    """
    event_id = start_id
    for name, detail in steps:
        event_id += 1
        step = StepEvent(name=name, status=StepStatus.OK, ms=0, detail=detail)
        yield ServerSentEvent(
            id=str(event_id), event="step", data=step.model_dump_json()
        )
        await asyncio.sleep(STEP_DELAY_S)

    event_id += 1
    done = DoneEvent(scan_id=DEMO_SCAN_ID, status=ScanStatus.DONE)
    yield ServerSentEvent(id=str(event_id), event="done", data=done.model_dump_json())


@router.get("/scans/{scan_id}/events")
async def scan_events(
    scan_id: str,
    after: Annotated[int, Query(ge=0)] = 0,
) -> EventSourceResponse:
    """Replay the recorded run's steps.

    The real stream replays from the start and the fix phase reopens it with
    ``?after=``; the same contract is honoured here, so the UI's two
    subscriptions behave as they do against a live backend.
    """
    _demo()
    if scan_id != DEMO_SCAN_ID:
        raise ApiException(ErrorCode.NOT_FOUND, f"no scan with id {scan_id!r}")
    steps = _PATCH_STEPS if after else _SCAN_STEPS
    return EventSourceResponse(_replay(steps, after))


@router.get("/history", response_model=None)
async def get_history(host: str | None = None) -> HistoryResponse:
    """One entry: the recorded run."""
    demo = _demo()
    scan = demo.after
    entry = HistoryEntry(
        id=DEMO_SCAN_ID,
        url=scan.url,
        host=scan.host,
        created_at=scan.created_at,
        scores=scan.before.scores if scan.before else None,
    )
    if host and host != scan.host:
        return HistoryResponse(scans=[], trend=[])
    return HistoryResponse(scans=[entry], trend=[])


# --------------------------------------------------------------------------
# Fix routes
# --------------------------------------------------------------------------


@router.post("/scans/{scan_id}/fixes", response_model=None)
async def generate_fixes(scan_id: str) -> JSONResponse:
    """The fixes the recorded run generated. No LLM call is made."""
    demo = _demo()
    if scan_id != DEMO_SCAN_ID:
        raise ApiException(ErrorCode.NOT_FOUND, f"no scan with id {scan_id!r}")
    patch = demo.after.patch
    if patch is None:
        raise ApiException(
            ErrorCode.LLM_UNAVAILABLE,
            "the recording holds no fixes",
        )
    if patch.ai_usage is None:
        raise ApiException(
            ErrorCode.LLM_UNAVAILABLE,
            "the recording holds no record of the AI usage behind its fixes",
        )
    body = FixesResponse(
        scan_id=DEMO_SCAN_ID, fixes=patch.fixes, ai_usage=patch.ai_usage
    ).model_dump(mode="json")
    body["_cached"] = {"label": demo.label, "live": False}
    return JSONResponse(body)


@router.post("/scans/{scan_id}/patch", status_code=202, response_model=None)
async def apply_patch(scan_id: str) -> PatchAccepted:
    """Accepted instantly: the patched result is already recorded."""
    _demo()
    if scan_id != DEMO_SCAN_ID:
        raise ApiException(ErrorCode.NOT_FOUND, f"no scan with id {scan_id!r}")
    # Any non-zero value makes the UI's second subscription pass `?after=`,
    # which is what selects the patch-phase steps above.
    return PatchAccepted(scan_id=DEMO_SCAN_ID, events_after=len(_SCAN_STEPS) + 1)
