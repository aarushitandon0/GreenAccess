"""BrowserSession tests (MASTERSPEC §6.1, CLAUDE.md security rules).

Real Chromium against a real local HTTP server, because what is under test is
exactly the browser-level behaviour: redirects re-validated by the SSRF route
guard, blocked subresources, and the page-weight abort.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest

from app.scanner.browser import BrowserSession, ScanAborted

pytestmark = pytest.mark.browser

METADATA = "http://169.254.169.254/latest/meta-data/"
HTML = {"Content-Type": "text/html; charset=utf-8"}


def _session(base: str, executable: str | None, **kwargs: object) -> BrowserSession:
    host_port = base.removeprefix("http://")
    return BrowserSession(
        allowed_local_hosts=(host_port,),
        executable_path=executable,
        nav_timeout_s=15,
        **kwargs,  # type: ignore[arg-type]
    )


async def test_load_measures_a_page(local_site: Callable, chromium_executable, tmp_path: Path):
    body = b"<!doctype html><title>Hi</title><p>" + b"x" * 5_000 + b"</p>"
    base = local_site({"/": (200, HTML, body)})

    async with _session(base, chromium_executable, screenshot_dir=tmp_path) as session:
        result = await session.load(base + "/")
        summary = session.collector.summarize(result.final_url)

    assert result.status == 200
    assert result.title == "Hi"
    assert await asyncio.to_thread(Path(result.screenshot_path).is_file)
    assert summary.request_count == 1
    # Transfer size = body plus response headers.
    assert len(body) < summary.total_bytes < len(body) + 1_000


async def test_redirect_into_the_metadata_range_is_blocked(
    local_site: Callable, chromium_executable
):
    base = local_site({"/": (302, {"Location": METADATA}, b"")})

    async with _session(base, chromium_executable) as session:
        with pytest.raises(ScanAborted) as excinfo:
            await session.load(base + "/")
        blocked = [url for url, _ in session.blocked_requests]

    assert excinfo.value.code == "URL_BLOCKED"
    assert METADATA in blocked


async def test_redirect_to_an_unlisted_private_host_is_blocked(
    local_site: Callable, chromium_executable
):
    """The allow-list names one host:port; a redirect to another port is refused."""
    other = local_site({"/": (200, HTML, b"<p>internal</p>")})
    base = local_site({"/": (302, {"Location": other + "/"}, b"")})

    async with _session(base, chromium_executable) as session:
        with pytest.raises(ScanAborted) as excinfo:
            await session.load(base + "/")

    assert excinfo.value.code == "URL_BLOCKED"


async def test_blocked_subresource_does_not_fail_the_page(
    local_site: Callable, chromium_executable
):
    page = f'<!doctype html><title>t</title><img src="{METADATA}x.png">'.encode()
    base = local_site({"/": (200, HTML, page)})

    async with _session(base, chromium_executable) as session:
        result = await session.load(base + "/")

    assert result.status == 200
    assert any(url.startswith(METADATA) for url, _ in result.blocked_requests)


async def test_page_weight_cap_aborts_the_scan(local_site: Callable, chromium_executable):
    # A valid script, so the browser downloads all of it (an invalid image is
    # abandoned part-way, which would not exercise the cap).
    page = b'<!doctype html><title>t</title><script src="/big.js"></script>'
    big = b"//" + b"x" * 3_000_000
    js = {"Content-Type": "text/javascript"}
    base = local_site({"/": (200, HTML, page), "/big.js": (200, js, big)})

    async with _session(base, chromium_executable, max_page_bytes=1_000_000) as session:
        with pytest.raises(ScanAborted) as excinfo:
            await session.load(base + "/")

    assert excinfo.value.code == "PAGE_TOO_LARGE"
    assert session.over_byte_cap is True
