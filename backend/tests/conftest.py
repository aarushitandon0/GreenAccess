"""Shared pytest fixtures.

``chromium_executable`` launches Chromium once per session to prove it works,
honouring ``PLAYWRIGHT_CHROMIUM_EXECUTABLE`` (see ``app/config.py``), and skips
browser tests when no browser can start.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest


@pytest.fixture(scope="session")
def chromium_executable() -> str | None:
    """The Chromium binary override, or None for Playwright's own. Skips if none work."""
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=executable)
            browser.close()
    except PlaywrightError as exc:
        pytest.skip(f"chromium is not available: {str(exc).splitlines()[0]}")
    return executable


# --------------------------------------------------------------------------- #
# A tiny local HTTP site, for tests that need real HTTP (redirects, byte counts)
# --------------------------------------------------------------------------- #

Routes = dict[str, tuple[int, dict[str, str], bytes]]


class _Handler(BaseHTTPRequestHandler):
    routes: ClassVar[Routes] = {}
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        status, headers, body = self.routes.get(
            self.path, (404, {"Content-Type": "text/plain"}, b"not found")
        )
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def local_site() -> Iterator[Callable[[Routes], str]]:
    """Start a threaded HTTP server for the given routes; returns its base URL."""
    servers: list[ThreadingHTTPServer] = []

    def start(routes: Routes) -> str:
        handler = type("RoutesHandler", (_Handler,), {"routes": routes})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()
