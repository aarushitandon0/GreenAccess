"""Scan pipeline orchestration (MASTERSPEC §4).

Single responsibility: run the scan steps in the order MASTERSPEC §4 fixes and
report each transition as a :class:`~app.models.StepEvent`, so the CLI can
print progress now and the SSE endpoint can stream the same events later.

Steps: ``validate, load, a11y, keyboard, aria, carbon, green`` run for real.
``score``, ``tradeoffs`` and ``persist`` belong to later phases; they are
emitted as ``skipped`` with a reason, and the result's ``Scores`` carries
``is_placeholder=True``, so nothing downstream can mistake the placeholder
numbers for computed ones.

Usage::

    pipeline = ScanPipeline(url, settings=get_settings())
    async for event in pipeline.events():
        ...                       # StepEvent(name, status, ms, detail)
    result = pipeline.result      # ScanResult

On failure, an ``error`` event for the failing step is yielded and then
:class:`ScanError` is raised, carrying a MASTERSPEC §12 error code.

Limits (CLAUDE.md, security rules): at most ``MAX_CONCURRENT_SCANS`` browser
scans at once per event loop, a ``SCAN_TIMEOUT_S`` deadline across all steps,
``NAV_TIMEOUT_S`` per navigation, and the ``MAX_PAGE_BYTES`` abort.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable
from importlib import metadata
from pathlib import Path
from typing import Literal, TypeVar

from playwright.async_api import Error as PlaywrightError

from app.carbon import green as green_client
from app.carbon.constants import ANIMATED_GIF_MIN_BYTES, CO2JS_VERSION, SWD_MODEL_VERSION
from app.carbon.detectors import DetectorReport, image_format, is_animated_gif, run_detectors
from app.carbon.report import build_carbon_result
from app.config import Settings, get_settings
from app.models import (
    A11yResult,
    CarbonResult,
    EngineVersions,
    ErrorCode,
    GreenResult,
    GreenSource,
    KeyboardResult,
    ScanResult,
    Scores,
    StepEvent,
    StepStatus,
)
from app.scanner import a11y, aria, keyboard
from app.scanner.browser import BrowserSession, LoadResult, ScanAborted
from app.scanner.dom import PageFacts, collect_page_facts
from app.scanner.network import NetworkSummary
from app.security.ssrf import UrlBlocked, ValidatedUrl, parse_ip_literal, validate_url

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Every step name MASTERSPEC §12 lists for a scan, in order.
SCAN_STEPS: tuple[str, ...] = (
    "validate",
    "load",
    "a11y",
    "keyboard",
    "aria",
    "carbon",
    "green",
    "score",
    "tradeoffs",
    "persist",
)

#: Steps that belong to later phases and are reported as skipped.
_NOT_YET_IMPLEMENTED: dict[str, str] = {
    "score": "scoring is not part of this phase; scores are placeholders",
    "tradeoffs": "trade-off engine is not part of this phase",
    "persist": "persistence is not part of this phase",
}

DEFAULT_SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "screenshots"

GreenChecker = Callable[[str], Awaitable[GreenResult]]


class ScanError(Exception):
    """A scan failed. `code` is one of the MASTERSPEC §12 error codes."""

    def __init__(self, code: ErrorCode, message: str, step: str = "") -> None:
        self.code = code
        self.message = message
        self.step = step
        super().__init__(message)


# One semaphore per event loop: asyncio primitives must not cross loops.
_semaphores: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


def _scan_slots(limit: int) -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    semaphore = _semaphores.get(loop)
    if semaphore is None:
        semaphore = asyncio.Semaphore(max(1, limit))
        _semaphores[loop] = semaphore
    return semaphore


def engine_versions() -> EngineVersions:
    """Versions of every engine that shaped a result (MASTERSPEC §5)."""
    try:
        playwright_version = metadata.version("playwright")
    except metadata.PackageNotFoundError:
        playwright_version = "unknown"
    version_file = a11y.VENDOR_DIR / "axe-version.json"
    try:
        axe_version = str(json.loads(version_file.read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError):
        axe_version = "unknown"
    return EngineVersions(
        playwright=playwright_version,
        axe=axe_version,
        swd_model=f"sustainable-web-design v{SWD_MODEL_VERSION} (CO2.js {CO2JS_VERSION} constants)",
    )


class ScanPipeline:
    """One scan of one URL. Create one per scan; it is not reusable."""

    def __init__(
        self,
        url: str,
        *,
        settings: Settings | None = None,
        screenshot_dir: Path | Literal["default"] | None = "default",
        green_checker: GreenChecker | None = None,
    ) -> None:
        self.url = url
        self.settings = settings or get_settings()
        #: None disables screenshots; "default" means DEFAULT_SCREENSHOT_DIR.
        self.screenshot_dir = (
            DEFAULT_SCREENSHOT_DIR if screenshot_dir == "default" else screenshot_dir
        )
        self._green_checker = green_checker or green_client.check_green_hosting
        self.result: ScanResult | None = None

        self._deadline = 0.0
        self._validated: ValidatedUrl | None = None
        self._load: LoadResult | None = None
        self._facts = PageFacts()
        self._a11y = A11yResult()
        self._keyboard = KeyboardResult()
        self._aria = ""
        self._summary: NetworkSummary | None = None
        self._report: DetectorReport | None = None
        self._carbon = CarbonResult()
        self._green: GreenResult | None = None

    # -- step plumbing ---------------------------------------------------- #

    async def _within_deadline(self, step: str, awaitable: Awaitable[T]) -> T:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise ScanError(
                ErrorCode.TIMEOUT,
                f"scan exceeded the {self.settings.scan_timeout_s}s limit before {step}",
                step,
            )
        try:
            return await asyncio.wait_for(awaitable, timeout=remaining)
        except TimeoutError as exc:
            raise ScanError(
                ErrorCode.TIMEOUT,
                f"scan exceeded the {self.settings.scan_timeout_s}s limit during {step}",
                step,
            ) from exc

    async def _step(self, name: str, run: Callable[[], Awaitable[str]]) -> AsyncIterator[StepEvent]:
        """Run one step, yielding its running and finished events."""
        yield StepEvent(name=name, status=StepStatus.RUNNING)
        started = time.monotonic()
        try:
            detail = await self._within_deadline(name, run())
        except ScanError as exc:
            exc.step = exc.step or name
            yield StepEvent(name=name, status=StepStatus.ERROR, ms=_ms(started), detail=exc.message)
            raise
        except ScanAborted as exc:
            error = ScanError(_error_code(exc.code), str(exc), name)
            yield StepEvent(name=name, status=StepStatus.ERROR, ms=_ms(started), detail=str(exc))
            raise error from exc
        except PlaywrightError as exc:
            message = f"{name} failed in the browser: {str(exc).splitlines()[0]}"
            yield StepEvent(name=name, status=StepStatus.ERROR, ms=_ms(started), detail=message)
            raise ScanError(ErrorCode.NAV_FAILED, message, name) from exc
        yield StepEvent(name=name, status=StepStatus.OK, ms=_ms(started), detail=detail)

    # -- the scan --------------------------------------------------------- #

    async def events(self) -> AsyncIterator[StepEvent]:
        """Run the scan, yielding a StepEvent per transition."""
        self._deadline = time.monotonic() + self.settings.scan_timeout_s

        async for event in self._step("validate", self._validate):
            yield event

        async with contextlib.AsyncExitStack() as stack:
            session = BrowserSession(
                nav_timeout_s=self.settings.nav_timeout_s,
                max_page_bytes=self.settings.max_page_bytes,
                allowed_local_hosts=self.settings.allowed_local_hosts,
                screenshot_dir=self.screenshot_dir,
                executable_path=self.settings.chromium_executable,
            )
            async for event in self._step("load", lambda: self._run_load(stack, session)):
                yield event
            async for event in self._step("a11y", lambda: self._run_a11y(session)):
                yield event
            async for event in self._step("keyboard", lambda: self._run_keyboard(session)):
                yield event
            async for event in self._step("aria", lambda: self._run_aria(session)):
                yield event
            async for event in self._step("carbon", lambda: self._run_carbon(session)):
                yield event

        async for event in self._step("green", self._run_green):
            yield event

        for name, reason in _NOT_YET_IMPLEMENTED.items():
            yield StepEvent(name=name, status=StepStatus.SKIPPED, detail=reason)

        self.result = self._assemble()

    async def run(self) -> ScanResult:
        """Run to completion without consuming events."""
        async for _event in self.events():
            pass
        assert self.result is not None
        return self.result

    # -- steps ------------------------------------------------------------ #

    async def _validate(self) -> str:
        try:
            self._validated = await asyncio.to_thread(
                validate_url, self.url, allowed_local_hosts=self.settings.allowed_local_hosts
            )
        except UrlBlocked as exc:
            raise ScanError(ErrorCode.URL_BLOCKED, str(exc), "validate") from exc
        return f"{self._validated.host} resolves to {', '.join(self._validated.resolved_ips)}"

    async def _run_load(self, stack: contextlib.AsyncExitStack, session: BrowserSession) -> str:
        assert self._validated is not None
        slots = _scan_slots(self.settings.max_concurrent_scans)
        await slots.acquire()
        stack.callback(slots.release)
        # Register cleanup before starting, so a failure or timeout part-way
        # through start-up still closes whatever did start.
        stack.push_async_exit(session.__aexit__)
        try:
            await session.__aenter__()
        except PlaywrightError as exc:
            raise ScanError(
                ErrorCode.NAV_FAILED,
                f"could not start the browser: {str(exc).splitlines()[0]}",
                "load",
            ) from exc

        self._load = await session.load(self._validated.url)
        # Facts are read straight after the load returns the page to the top,
        # before the keyboard crawl moves focus and scroll position around.
        self._facts = await collect_page_facts(session.page)
        return (
            f"HTTP {self._load.status}, {session.collector.total_bytes:,} bytes, "
            f"{self._load.scroll_steps} scroll steps"
        )

    async def _run_a11y(self, session: BrowserSession) -> str:
        self._a11y = await a11y.run_axe(session.page)
        return f"{self._a11y.unique_rules} rules violated on {self._a11y.total_nodes} nodes"

    async def _run_keyboard(self, session: BrowserSession) -> str:
        self._keyboard = await keyboard.crawl(session.page)
        trap = (
            f"trap in {self._keyboard.trap_container}"
            if self._keyboard.trap_detected
            else "no trap"
        )
        return f"{self._keyboard.tabs_pressed} Tab presses, {trap}"

    async def _run_aria(self, session: BrowserSession) -> str:
        self._aria = await aria.snapshot(session.page)
        return f"{len(self._aria):,} characters"

    async def _run_carbon(self, session: BrowserSession) -> str:
        assert self._load is not None
        page_url = self._load.final_url or self._load.url
        self._summary = session.collector.summarize(page_url)
        animated = await self._animated_gif_urls(session, self._summary)
        self._report = run_detectors(
            self._summary, self._facts, page_url=page_url, animated_gif_urls=animated
        )
        self._carbon = build_carbon_result(self._summary, self._report, green=False)
        return (
            f"{self._carbon.total_bytes:,} bytes, {self._carbon.request_count} requests, "
            f"{len(self._carbon.detections)} detections"
        )

    async def _animated_gif_urls(
        self, session: BrowserSession, summary: NetworkSummary
    ) -> frozenset[str]:
        """Heavy GIFs whose already-downloaded body has more than one frame."""
        animated: set[str] = set()
        for record in summary.records:
            if record.failed or record.transfer_bytes <= ANIMATED_GIF_MIN_BYTES:
                continue
            if image_format(record.url, record.mime_type) != "gif":
                continue
            body = await session.response_body(record.request_id)
            if body is not None and is_animated_gif(body):
                animated.add(record.url)
        return frozenset(animated)

    async def _run_green(self) -> str:
        assert self._validated is not None
        host = self._validated.host
        # A local development host, or a bare IP, is not something the Green Web
        # Foundation can answer for, and naming internal hosts to a third party
        # leaks them. Report it as unavailable ("unknown"), never as green.
        if self._validated.allowed_by_override or parse_ip_literal(host) is not None:
            self._green = GreenResult(host=host, green=False, source=GreenSource.UNAVAILABLE)
            return "skipped lookup for a local or IP host; reported as unknown"

        self._green = await self._green_checker(host)
        if self._green.green and self._summary is not None and self._report is not None:
            self._carbon = build_carbon_result(self._summary, self._report, green=True)
        if self._green.source is GreenSource.UNAVAILABLE:
            return "Green Web Foundation unavailable; reported as unknown"
        return "green host" if self._green.green else "not a verified green host"

    def _assemble(self) -> ScanResult:
        assert self._validated is not None
        return ScanResult(
            scores=Scores(is_placeholder=True),
            a11y=self._a11y,
            keyboard=self._keyboard,
            carbon=self._carbon,
            green=self._green or GreenResult(host=self._validated.host),
            tradeoffs=[],
            aria_snapshot=self._aria,
            screenshot_path=self._load.screenshot_path if self._load else "",
            engine_versions=engine_versions(),
        )


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _error_code(code: str) -> ErrorCode:
    try:
        return ErrorCode(code)
    except ValueError:
        return ErrorCode.NAV_FAILED
