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
import base64
import binascii
import contextlib
import logging
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
from app.security.egress_proxy import (
    BLOCKED_HEADER,
    PROXY_ERROR_HEADER,
    EgressProxy,
    host_port,
)
from app.security.ssrf import make_request_guard

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
        self.executable_path = executable_path

        self.collector = NetworkCollector()
        self.blocked_requests: list[tuple[str, str]] = []
        #: Set the moment live bytes cross the cap; from then on every new
        #: request is aborted and the next checkpoint raises PAGE_TOO_LARGE.
        self.over_byte_cap = False
        #: A top-level navigation (the page itself or a redirect of it) that
        #: the SSRF guard refused, with the reason.
        self.blocked_navigation: tuple[str, str] | None = None

        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._cdp: CDPSession | None = None
        self._proxy: EgressProxy | None = None
        #: URL of the latest top-level navigation request, redirect hops included.
        self._last_navigation = ""
        self._stop_task: asyncio.Task[None] | None = None

    # -- lifecycle -------------------------------------------------------- #

    async def __aenter__(self) -> BrowserSession:
        # Every browser connection goes through the SSRF-enforcing proxy, which
        # is what catches redirect hops the route guard below never sees.
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

        # First layer: re-validate every request the page starts, and record it.
        # Redirect hops are not routed by Playwright; the egress proxy covers them.
        ssrf_guard = make_request_guard(
            allowed_local_hosts=self.allowed_local_hosts,
            on_block=self._record_blocked,
        )

        async def route_handler(route: Route, request: Request) -> None:
            if self.over_byte_cap:
                self._record_blocked(request.url, "page-weight cap exceeded")
                await route.abort("blockedbyclient")
                return
            if request.is_navigation_request() and request.frame.parent_frame is None:
                blocked_before = len(self.blocked_requests)
                await ssrf_guard(route, request)
                if len(self.blocked_requests) > blocked_before:
                    self.blocked_navigation = self.blocked_requests[-1]
                return
            await ssrf_guard(route, request)

        await self._context.route("**/*", route_handler)

        self._page = await self._context.new_page()
        self._page.on("request", self._on_request)
        await self._attach_cdp(self._page)
        return self

    def _on_request(self, request: Request) -> None:
        with contextlib.suppress(PlaywrightError):
            if request.is_navigation_request() and request.frame.parent_frame is None:
                self._last_navigation = request.url

    async def __aexit__(self, *exc: object) -> None:
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

        self._cdp = session

    def _watch_byte_cap(self) -> None:
        """Called on every CDP network event: trip the cap as soon as it is hit.

        Raising here is impossible (this runs in an event callback), so it sets
        a flag, stops the page loading, and lets the route handler refuse every
        further request. :meth:`_check_byte_cap` then turns it into an error.
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

    async def response_body(self, request_id: str) -> bytes | None:
        """The body of a finished response, from the CDP buffer, or None.

        Reads what the browser already downloaded; it never re-fetches. Used
        sparingly (only to check whether a heavy GIF is animated).
        """
        if self._cdp is None:
            return None
        try:
            reply = await self._cdp.send("Network.getResponseBody", {"requestId": request_id})
        except PlaywrightError as exc:
            logger.info("response body for %s unavailable: %s", request_id, exc)
            return None
        body = str(reply.get("body") or "")
        if reply.get("base64Encoded"):
            try:
                return base64.b64decode(body)
            except (binascii.Error, ValueError):
                return None
        return body.encode("utf-8")

    def _check_navigation_blocked(self) -> None:
        """Raise URL_BLOCKED if the top-level navigation, or any redirect hop
        of it, was refused by the route guard or the egress proxy."""
        if self.blocked_navigation is not None:
            blocked_url, reason = self.blocked_navigation
            raise ScanAborted("URL_BLOCKED", f"navigation to {blocked_url} was blocked: {reason}")
        if self._proxy is None or not self._last_navigation:
            return
        target = host_port(self._last_navigation)
        for blocked, reason in self._proxy.blocked:
            if host_port(blocked) == target:
                raise ScanAborted(
                    "URL_BLOCKED",
                    f"navigation redirected to {self._last_navigation}, which was blocked: {reason}",
                )

    def _check_byte_cap(self) -> None:
        """Abort once the page exceeds the weight cap (MASTERSPEC §6.1)."""
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
            self._check_navigation_blocked()
            raise ScanAborted("NAV_FAILED", f"could not load {url}: {exc}") from exc

        if response is None:
            raise ScanAborted("NAV_FAILED", f"no response for {url}")

        # A redirect the proxy refused arrives as its 403, not as an error.
        if response.headers.get(BLOCKED_HEADER.lower()):
            self._last_navigation = response.url
        self._check_navigation_blocked()
        # Any other page the proxy wrote itself (e.g. 502, host unreachable) is
        # not the site; scanning it would report on our own error page.
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
