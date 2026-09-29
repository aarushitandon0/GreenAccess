"""Browser lifecycle for a scan (MASTERSPEC §6.1).

Owns the Chromium context, the page load, the full-page scroll that triggers
lazy content, the screenshot, and the byte-cap abort.

Every scan gets a fresh context with no cache (MASTERSPEC §6.1), so two scans of
the same URL measure the same bytes rather than the second one measuring a warm
cache.

The CDP session is opened here and its ``Network.*`` events are forwarded to
:class:`app.scanner.network.NetworkCollector`, because the collector needs to be
listening before navigation starts or the document request itself is missed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    CDPSession,
    Page,
    Playwright,
    Request,
    Response,
    Route,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.scanner.network import NetworkCollector
from app.security.egress_proxy import BLOCKED_HEADER, PROXY_ERROR_HEADER, EgressProxy
from app.security.ssrf import check_browser_request, make_request_guard

logger = logging.getLogger(__name__)

# MASTERSPEC §6.1.
VIEWPORT_WIDTH = 1366
VIEWPORT_HEIGHT = 768
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 GreenAccessBot/0.1"
)

# Full-page scroll: one viewport at a time, 250 ms apart, at most 30 steps.
SCROLL_PAUSE_MS = 250
MAX_SCROLL_STEPS = 30

# Cap on waiting for networkidle.
NETWORK_IDLE_TIMEOUT_MS = 15_000


class ScanAborted(Exception):
    """A scan stopped early. `code` maps to the API errors in MASTERSPEC §12."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass
class LoadResult:
    """What a page load produced."""

    url: str
    final_url: str
    status: int = 0
    title: str = ""
    html: str = ""
    screenshot_path: str = ""
    scroll_steps: int = 0
    document_height: int = 0
    blocked_requests: list[tuple[str, str]] = field(default_factory=list)


class BrowserSession:
    """A single scan's browser context.

    Use as an async context manager::

        async with BrowserSession(settings) as session:
            result = await session.load("https://example.com")
    """

    def __init__(
        self,
        *,
        nav_timeout_s: int = 30,
        max_page_bytes: int = 26_214_400,
        allowed_local_hosts: tuple[str, ...] = (),
        screenshot_dir: Path | None = None,
        headless: bool = True,
        executable_path: str | None = None,
    ) -> None:
        self.nav_timeout_s = nav_timeout_s
        self.max_page_bytes = max_page_bytes
        self.allowed_local_hosts = allowed_local_hosts
        self.screenshot_dir = screenshot_dir
        self.headless = headless
        # Explicit argument first, then the environment, so every construction
        # site (pipeline, API, tests) honours the override without plumbing.
        self.executable_path = executable_path or os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")

        self.collector = NetworkCollector()
        self.blocked_requests: list[tuple[str, str]] = []
        #: Set the moment live bytes cross the cap; from then on every new
        #: request is aborted and the next checkpoint raises PAGE_TOO_LARGE.
        self.over_byte_cap = False

        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._cdp: CDPSession | None = None
        #: In-flight redirect-guard checks, kept referenced until they finish.
        self._guard_tasks: set[asyncio.Task[None]] = set()
        self._proxy: EgressProxy | None = None
        self._stop_task: asyncio.Task[None] | None = None

    # -- lifecycle -------------------------------------------------------- #

    async def __aenter__(self) -> BrowserSession:
        # Network-level backstop for the Fetch guard below: every browser
        # connection goes through a proxy that re-validates the target and
        # connects only to the address it validated. That closes the DNS
        # rebinding window a Fetch.continueRequest leaves (Chromium resolves
        # again after the check) and covers connections a page-level CDP
        # session never sees (out-of-process iframes, workers).
        self._proxy = EgressProxy(
            allowed_local_hosts=self.allowed_local_hosts,
            on_block=self._record_blocked,
        )
        await self._proxy.__aenter__()

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            executable_path=self.executable_path or None,
            # "<-loopback>" removes Chromium's implicit proxy bypass for
            # localhost/127.0.0.1, so local targets are checked too.
            proxy={"server": self._proxy.url, "bypass": "<-loopback>"},
            args=[
                "--disable-dev-shm-usage",
                # Deterministic rendering across machines.
                "--force-color-profile=srgb",
                "--disable-lcd-text",
                # WebRTC UDP does not go through an HTTP proxy; forbid it so a
                # page cannot use STUN/TURN to reach internal addresses.
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            ],
        )
        self._context = await self._browser.new_context(
            viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
            user_agent=USER_AGENT,
            # MASTERSPEC §6.1: animations must run, so we do not force reduced
            # motion. The page's own handling of it is what we are auditing.
            reduced_motion="no-preference",
            bypass_csp=False,
            ignore_https_errors=False,
            java_script_enabled=True,
        )
        self._context.set_default_timeout(self.nav_timeout_s * 1000)
        self._context.set_default_navigation_timeout(self.nav_timeout_s * 1000)

        # Re-validate every request, including redirects (CLAUDE.md security).
        ssrf_guard = make_request_guard(
            allowed_local_hosts=self.allowed_local_hosts,
            on_block=self._record_blocked,
        )

        async def route_handler(route: Route, request: Request) -> None:
            if self.over_byte_cap:
                self._record_blocked(request.url, "page-weight cap exceeded")
                await route.abort("blockedbyclient")
                return
            await ssrf_guard(route, request)

        await self._context.route("**/*", route_handler)

        self._page = await self._context.new_page()
        await self._attach_cdp(self._page)
        return self

    async def __aexit__(self, *exc: object) -> None:
        for task in list(self._guard_tasks):
            task.cancel()
        if self._stop_task is not None and not self._stop_task.done():
            self._stop_task.cancel()
        for closer in (
            getattr(self._context, "close", None),
            getattr(self._browser, "close", None),
            getattr(self._playwright, "stop", None),
        ):
            if closer is None:
                continue
            with contextlib.suppress(PlaywrightError, RuntimeError):
                await closer()
        if self._proxy is not None:
            await self._proxy.__aexit__(None, None, None)

    def _record_blocked(self, url: str, reason: str) -> None:
        # Both guards see a first hop, so the same block can be reported twice.
        if (url, reason) in self.blocked_requests:
            return
        logger.info("blocked request %s: %s", url, reason)
        self.blocked_requests.append((url, reason))

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("BrowserSession is not started; use `async with`")
        return self._page

    # -- CDP -------------------------------------------------------------- #

    async def _attach_cdp(self, page: Page) -> None:
        """Enable Network domain and forward events to the collector."""
        session = await page.context.new_cdp_session(page)
        await session.send("Network.enable")

        for event in (
            "Network.requestWillBeSent",
            "Network.responseReceived",
            "Network.dataReceived",
            "Network.loadingFinished",
            "Network.loadingFailed",
            "Network.requestServedFromCache",
        ):
            # Bind `event` per iteration rather than closing over the loop var.
            def make_handler(name: str):  # noqa: ANN202
                def handler(params: dict[str, Any]) -> None:
                    self.collector.handle(name, params)
                    self._watch_byte_cap()

                return handler

            session.on(event, make_handler(event))

        # Playwright's route handler is only called for the first URL of a
        # redirect chain, so without this a public page could 302 the browser
        # to a private or metadata address unchecked. CDP Fetch pauses every
        # hop, redirects included, and each one is re-validated before it is
        # allowed onto the network (CLAUDE.md: "re-check after redirects").
        self._cdp = session
        session.on("Fetch.requestPaused", self._on_request_paused)
        await session.send(
            "Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]}
        )

    def _on_request_paused(self, params: dict[str, Any]) -> None:
        """CDP event callback. The decision needs DNS, so it runs as a task."""
        task = asyncio.create_task(self._resolve_paused(params))
        self._guard_tasks.add(task)
        task.add_done_callback(self._guard_tasks.discard)

    async def _resolve_paused(self, params: dict[str, Any]) -> None:
        request_id = params.get("requestId", "")
        url = params.get("request", {}).get("url", "")
        reason = await check_browser_request(url, allowed_local_hosts=self.allowed_local_hosts)
        if reason is not None:
            self._record_blocked(url, reason)
        method, payload = (
            ("Fetch.continueRequest", {"requestId": request_id})
            if reason is None
            else ("Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"})
        )
        if self._cdp is None:  # pragma: no cover - set before Fetch is enabled
            return
        try:
            await self._cdp.send(method, payload)
        except PlaywrightError as exc:
            # The page or context closed while the check ran; nothing to resume.
            logger.debug("could not resolve paused request %s: %s", url, exc)

    def _watch_byte_cap(self) -> None:
        """Called on every CDP network event: trip the cap as soon as it is hit.

        Raising is impossible in an event callback, so this sets a flag, stops
        the page loading, and lets the route handler refuse further requests.
        :meth:`_check_byte_cap` turns the flag into PAGE_TOO_LARGE.
        """
        if self.over_byte_cap or self.collector.live_bytes <= self.max_page_bytes:
            return
        self.over_byte_cap = True
        logger.warning("page exceeded the %s-byte cap; stopping the load", self.max_page_bytes)
        if self._cdp is not None:
            self._stop_task = asyncio.ensure_future(self._stop_loading())

    async def _stop_loading(self) -> None:
        if self._cdp is None:
            return
        with contextlib.suppress(PlaywrightError):
            await self._cdp.send("Page.stopLoading")

    def _check_byte_cap(self) -> None:
        """Abort once the page exceeds the weight cap (MASTERSPEC §6.1).

        Counts in-flight and aborted downloads too (``live_bytes``), so one
        huge response cannot slip past the cap until it finishes.
        """
        total = max(self.collector.total_bytes, self.collector.live_bytes)
        if self.over_byte_cap or total > self.max_page_bytes:
            raise ScanAborted(
                "PAGE_TOO_LARGE",
                f"page exceeded the {self.max_page_bytes:,}-byte cap at {total:,} bytes",
            )

    # -- loading ---------------------------------------------------------- #

    async def load(self, url: str) -> LoadResult:
        """Navigate, settle, scroll the full page, and screenshot it."""
        page = self.page
        self.collector.page_url = url

        try:
            response: Response | None = await page.goto(
                url, wait_until="domcontentloaded", timeout=self.nav_timeout_s * 1000
            )
        except PlaywrightTimeoutError as exc:
            raise ScanAborted("TIMEOUT", f"navigation to {url} timed out") from exc
        except PlaywrightError as exc:
            # A load stopped by the byte cap surfaces as a navigation error.
            self._check_byte_cap()
            blocked_markers = ("ERR_BLOCKED_BY_CLIENT", "ERR_TUNNEL_CONNECTION_FAILED")
            if any(marker in str(exc) for marker in blocked_markers) and self.blocked_requests:
                # The document itself, or a hop it redirected to, failed the
                # SSRF guard. That is a blocked URL, not a navigation failure.
                blocked_url, reason = self.blocked_requests[-1]
                raise ScanAborted(
                    "URL_BLOCKED", f"{url} led to a blocked address {blocked_url}: {reason}"
                ) from exc
            raise ScanAborted("NAV_FAILED", f"could not load {url}: {exc}") from exc

        if response is None:
            raise ScanAborted("NAV_FAILED", f"no response for {url}")

        # The egress proxy answers for itself with marked responses: a 403 when
        # the SSRF policy refused the target, or a 502 when the host was
        # unreachable. Neither is the site's page, so neither may be scanned.
        blocked = response.headers.get(BLOCKED_HEADER.lower())
        if blocked:
            raise ScanAborted("URL_BLOCKED", f"{url} led to {response.url}, blocked: {blocked}")
        proxy_error = response.headers.get(PROXY_ERROR_HEADER.lower())
        if proxy_error:
            raise ScanAborted("NAV_FAILED", f"could not load {url}: {proxy_error}")

        self._check_byte_cap()

        # Settle, but never let a page that keeps polling hold the scan open.
        with contextlib.suppress(PlaywrightTimeoutError):
            await page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_TIMEOUT_MS)

        self._check_byte_cap()

        scroll_steps, document_height = await self._scroll_full_page(page)
        self._check_byte_cap()

        screenshot_path = await self._screenshot(page, url)

        return LoadResult(
            url=url,
            final_url=page.url,
            status=response.status,
            title=await page.title(),
            html=await page.content(),
            screenshot_path=screenshot_path,
            scroll_steps=scroll_steps,
            document_height=document_height,
            blocked_requests=list(self.blocked_requests),
        )

    async def _scroll_full_page(self, page: Page) -> tuple[int, int]:
        """Scroll by viewport steps to trigger lazy content, then return to top.

        MASTERSPEC §6.1: step by viewport height, pause 250 ms, up to 30 steps.
        """
        document_height = await page.evaluate("document.body.scrollHeight")
        steps = 0

        for step in range(1, MAX_SCROLL_STEPS + 1):
            await page.evaluate(f"window.scrollTo(0, {step * VIEWPORT_HEIGHT})")
            await asyncio.sleep(SCROLL_PAUSE_MS / 1000)
            steps = step
            self._check_byte_cap()

            at_bottom = await page.evaluate(
                "(window.innerHeight + window.scrollY) >= document.body.scrollHeight - 2"
            )
            if at_bottom:
                break

        # Lazy content may have lengthened the document.
        document_height = await page.evaluate("document.body.scrollHeight")

        await page.evaluate("window.scrollTo(0, 0)")
        await asyncio.sleep(SCROLL_PAUSE_MS / 1000)

        with contextlib.suppress(PlaywrightTimeoutError):
            await page.wait_for_load_state("networkidle", timeout=5_000)

        return steps, int(document_height or 0)

    async def _screenshot(self, page: Page, url: str) -> str:
        """Full-page screenshot. A failure here must not fail the scan."""
        if self.screenshot_dir is None:
            return ""
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() else "-" for c in url)[:80].strip("-")
        path = self.screenshot_dir / f"{safe or 'page'}.png"
        try:
            await page.screenshot(path=str(path), full_page=True, timeout=20_000)
        except (PlaywrightError, PlaywrightTimeoutError) as exc:
            logger.warning("screenshot failed for %s: %s", url, exc)
            return ""
        return str(path)
