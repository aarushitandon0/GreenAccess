"""Scan routes (MASTERSPEC §12).

Single responsibility: the HTTP surface for creating, following, reading and
listing scans. The work itself happens in :mod:`app.api.runner`; this module
validates input, applies the rate limit and SSRF guard, and shapes responses.

    POST /api/scans                  -> 202 {scan_id}
    GET  /api/scans/{id}             -> Scan
    GET  /api/scans/{id}/events      -> SSE: step, done, error
    GET  /api/scans/{id}/screenshot  -> PNG (?state=before|after)
    POST /api/scans/{id}/cancel      -> Scan  (addition to §12, for §13's cancel button)
    GET  /api/history                -> HistoryResponse (?host=)
    GET  /api/demo                   -> DemoInfo
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import FileResponse
from sse_starlette import EventSourceResponse, ServerSentEvent

from app.api.errors import ApiException
from app.api.events import ScanEvent
from app.api.ratelimit import SCANS_PER_MINUTE
from app.api.state import AppState, get_state
from app.models import (
    ApiError,
    ApiErrorEnvelope,
    DemoInfo,
    DoneEvent,
    ErrorCode,
    HistoryEntry,
    HistoryResponse,
    Scan,
    ScanCreated,
    ScanRequest,
    ScanStatus,
    TrendPoint,
    Weights,
)
from app.scoring.constants import WEIGHT_UI_MAX, WEIGHT_UI_MIN
from app.security.ssrf import UrlBlocked

logger = logging.getLogger(__name__)

__all__ = ["router"]

router = APIRouter(prefix="/api", tags=["scans"])

State = Annotated[AppState, Depends(get_state)]

#: Seconds between SSE keep-alive comments, so proxies do not drop a quiet stream.
SSE_PING_S: Final[int] = 15

#: Browser reconnect delay sent with each event, in milliseconds.
SSE_RETRY_MS: Final[int] = 2000


def _errors(*codes: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI entries documenting the error envelope for these statuses."""
    return {code: {"model": ApiErrorEnvelope} for code in codes}


async def _load(state: AppState, scan_id: str) -> Scan:
    scan = await state.repository.get(scan_id)
    if scan is None:
        raise ApiException(ErrorCode.NOT_FOUND, f"no scan with id {scan_id!r}")
    return scan


def _checked_weights(weights: Weights | None) -> Weights:
    """Default the weights, and hold them to the UI toggle's range (§9.3)."""
    if weights is None:
        return Weights()
    for name, value in (("a11y", weights.a11y), ("carbon", weights.carbon)):
        if not WEIGHT_UI_MIN <= value <= WEIGHT_UI_MAX:
            raise ApiException(
                ErrorCode.INVALID_REQUEST,
                f"weights.{name} must be between {WEIGHT_UI_MIN} and {WEIGHT_UI_MAX}, got {value}",
            )
    return weights


# --------------------------------------------------------------------------- #
# Create
# --------------------------------------------------------------------------- #


@router.post(
    "/scans",
    status_code=202,
    response_model=ScanCreated,
    responses=_errors(400, 422, 429),
    summary="Start a scan",
)
async def create_scan(
    body: ScanRequest, request: Request, response: Response, state: State
) -> ScanCreated:
    """Validate the URL (and every redirect it leads to), then queue the scan.

    Rejected with ``URL_BLOCKED`` before anything is queued if the URL, or any
    hop of its redirect chain, is private, loopback, link-local, a metadata
    address or not http(s). Every request reaching this handler counts toward
    the rate limit, blocked ones included, so the endpoint cannot be used to
    probe addresses faster than scans are allowed.
    """
    client = request.client.host if request.client else "unknown"
    retry_after = state.limiter.hit(client)
    if retry_after is not None:
        raise ApiException(
            ErrorCode.RATE_LIMITED,
            f"at most {SCANS_PER_MINUTE} scans per minute; try again in {retry_after} s",
            headers={"Retry-After": str(retry_after)},
        )

    weights = _checked_weights(body.weights)
    url = str(body.url)

    try:
        validated = await state.preflight(url, state.settings.allowed_local_hosts)
    except UrlBlocked as blocked:
        raise ApiException(ErrorCode.URL_BLOCKED, blocked.reason) from blocked

    scan = Scan(id=uuid.uuid4().hex, url=url, host=validated.host)
    await state.runner.submit(scan, weights)
    response.headers["Location"] = f"/api/scans/{scan.id}"
    return ScanCreated(scan_id=scan.id)


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #


@router.get("/scans/{scan_id}", response_model=Scan, responses=_errors(404), summary="Get a scan")
async def get_scan(scan_id: str, state: State) -> Scan:
    return await _load(state, scan_id)


def _terminal_events(scan: Scan) -> list[ScanEvent]:
    """Events for a scan whose live log is gone (evicted, or before a restart)."""
    if scan.status is ScanStatus.DONE:
        payload = DoneEvent(scan_id=scan.id, status=scan.status)
        return [ScanEvent(id=1, event="done", data=payload.model_dump_json())]
    error = scan.error or ApiError(
        code=ErrorCode.NAV_FAILED,
        message="the scan is no longer running and left no result",
    )
    return [ScanEvent(id=1, event="error", data=error.model_dump_json())]


@router.get(
    "/scans/{scan_id}/events",
    responses={
        200: {
            "description": (
                "Server-sent events. `step` carries a StepEvent, `done` a DoneEvent, "
                "`error` an ApiError. The stream ends after `done` or `error`. Every "
                "event has a numeric `id`; reconnect with `Last-Event-ID` to resume."
            ),
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
        **_errors(404),
    },
    summary="Follow a scan's progress",
)
async def scan_events(
    scan_id: str,
    state: State,
    last_event_id: Annotated[str | None, Header()] = None,
    after: Annotated[int | None, Query(ge=0)] = None,
) -> EventSourceResponse:
    """Replay everything so far, then stream live until the scan ends.

    ``after`` does what ``Last-Event-ID`` does, for clients that cannot set
    headers (a browser ``EventSource``): follow a fix or patch phase from the
    id ``POST /patch`` returned.
    """
    log = state.hub.get(scan_id)
    replay: list[ScanEvent] | None = None
    if log is None:
        replay = _terminal_events(await _load(state, scan_id))

    try:
        resume = max(0, int(last_event_id)) if last_event_id else 0
    except ValueError:
        resume = 0
    start = max(resume, after or 0)

    async def stream() -> AsyncIterator[ServerSentEvent]:
        source = log.subscribe(start) if log is not None else _iterate(replay or [], start)
        async for item in source:
            yield ServerSentEvent(
                data=item.data, event=item.event, id=str(item.id), retry=SSE_RETRY_MS
            )

    return EventSourceResponse(
        stream(),
        ping=SSE_PING_S,
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _iterate(events: list[ScanEvent], after: int) -> AsyncIterator[ScanEvent]:
    for item in events:
        if item.id > after:
            yield item


@router.get(
    "/scans/{scan_id}/screenshot",
    response_class=FileResponse,
    responses={200: {"content": {"image/png": {}}}, **_errors(404, 422)},
    summary="Full-page screenshot",
)
async def scan_screenshot(
    scan_id: str,
    state: State,
    which: Annotated[Literal["before", "after"], Query(alias="state")] = "before",
) -> FileResponse:
    scan = await _load(state, scan_id)
    result = scan.before if which == "before" else scan.after
    raw = result.screenshot_path if result is not None else ""
    if not raw:
        raise ApiException(ErrorCode.NOT_FOUND, f"no {which} screenshot for scan {scan_id!r}")

    path = await asyncio.to_thread(_servable_screenshot, raw, state.settings.screenshot_dir)
    if path is None:
        raise ApiException(ErrorCode.NOT_FOUND, f"no {which} screenshot for scan {scan_id!r}")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private"})


def _servable_screenshot(raw: str, screenshot_dir: Path) -> Path | None:
    """`raw` as a file to serve, or None. Blocking filesystem work; run in a thread.

    The path comes from our own database, but it is still a path read off disk
    on a request: only ever serve regular files inside the screenshot folder.
    """
    root = screenshot_dir.resolve()
    path = Path(raw).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    return path


# --------------------------------------------------------------------------- #
# Cancel (addition to §12: §13's Scanning screen has a cancel button)
# --------------------------------------------------------------------------- #


@router.post(
    "/scans/{scan_id}/cancel",
    response_model=Scan,
    responses=_errors(404),
    summary="Cancel a queued or running scan",
)
async def cancel_scan(scan_id: str, state: State) -> Scan:
    """Idempotent: cancelling a finished scan returns it unchanged."""
    await _load(state, scan_id)
    await state.runner.cancel(scan_id)
    return await _load(state, scan_id)


# --------------------------------------------------------------------------- #
# History and demo
# --------------------------------------------------------------------------- #


def _history_entry(scan: Scan) -> HistoryEntry:
    result = scan.before if scan.status is ScanStatus.DONE else None
    scores = result.scores if result is not None and not result.scores.is_placeholder else None
    return HistoryEntry(
        id=scan.id,
        url=scan.url,
        host=scan.host,
        status=scan.status,
        created_at=scan.created_at,
        a11y=scores.a11y if scores else None,
        carbon=scores.carbon if scores else None,
        combined=scores.combined if scores else None,
        carbon_grade=scores.carbon_grade if scores else None,
        grams_per_view=result.carbon.grams_per_view if result is not None else None,
    )


@router.get("/history", response_model=HistoryResponse, summary="Past scans and per-host trend")
async def history(
    state: State,
    host: Annotated[str | None, Query(max_length=253)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> HistoryResponse:
    wanted = host.strip().lower().rstrip(".") if host and host.strip() else None
    scans = await state.repository.list_recent(host=wanted, limit=limit)
    entries = [_history_entry(scan) for scan in scans]

    trends: dict[str, list[TrendPoint]] = {}
    for entry in reversed(entries):  # oldest first, for a left-to-right sparkline
        if entry.combined is not None:
            trends.setdefault(entry.host, []).append(
                TrendPoint(scan_id=entry.id, created_at=entry.created_at, combined=entry.combined)
            )
    return HistoryResponse(host=wanted, scans=entries, trends=trends)


@router.get("/demo", response_model=DemoInfo, summary="Where the Daily Herald demo is served")
async def demo(state: State) -> DemoInfo:
    return DemoInfo(url=state.settings.demo_url)
