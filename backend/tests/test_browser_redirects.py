"""The browser's SSRF guard must re-check every redirect hop (CLAUDE.md security).

Regression test: Playwright's route handler only sees the first URL of a
redirect chain, so before the CDP Fetch guard existed an allow-listed page could
302 the browser to a private address and the scanner would load it.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.scanner.browser import BrowserSession, ScanAborted
from tests.api_support import RedirectServer
from tests.test_demo_integration import _chromium_available

pytestmark = pytest.mark.browser


@pytest.fixture(scope="module", autouse=True)
def _needs_chromium() -> Iterator[None]:
    if not _chromium_available():
        pytest.skip("chromium is not installed")
    yield


async def test_document_redirect_to_a_private_address_is_url_blocked() -> None:
    target = "http://127.0.0.2:9/secret"
    with RedirectServer(target) as server:
        allowed = (f"localhost:{server.port}",)
        async with BrowserSession(nav_timeout_s=10, allowed_local_hosts=allowed) as session:
            with pytest.raises(ScanAborted) as caught:
                await session.load(f"http://localhost:{server.port}/")
            blocked = [url for url, _ in session.blocked_requests]

    assert caught.value.code == "URL_BLOCKED"
    assert target in blocked


class _PageWithRedirectingImage(RedirectServer):
    """Serves a page whose <img> 302s to the cloud metadata address."""

    def __init__(self) -> None:
        super().__init__(lambda _: "")
        page = b'<html><body><h1>hi</h1><img src="/img" alt=""></body></html>'
        handler = self.server.RequestHandlerClass
        original = handler.do_GET

        def do_get(request) -> None:
            if request.path == "/img":
                request.send_response(302)
                request.send_header("Location", "http://169.254.169.254/latest/meta-data/")
                request.send_header("Content-Length", "0")
                request.end_headers()
                return
            if request.path == "/":
                request.send_response(200)
                request.send_header("Content-Type", "text/html")
                request.send_header("Content-Length", str(len(page)))
                request.end_headers()
                request.wfile.write(page)
                return
            original(request)

        handler.do_GET = do_get


async def test_subresource_redirect_to_metadata_is_blocked_and_the_scan_continues() -> None:
    with _PageWithRedirectingImage() as server:
        allowed = (f"localhost:{server.port}",)
        async with BrowserSession(nav_timeout_s=10, allowed_local_hosts=allowed) as session:
            result = await session.load(f"http://localhost:{server.port}/")

    assert result.status == 200
    assert any("169.254.169.254" in url for url, _ in result.blocked_requests)
