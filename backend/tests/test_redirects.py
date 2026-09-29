"""Redirect pre-flight (security/redirects.py) against real local servers."""

from __future__ import annotations

import socket

import pytest

from app.security.redirects import preflight_redirects
from app.security.ssrf import UrlBlocked
from tests.api_support import RedirectServer


async def test_no_redirect_passes_and_returns_the_submitted_url() -> None:
    with RedirectServer("") as server:
        url = f"http://localhost:{server.port}/page"
        validated = await preflight_redirects(
            url, allowed_local_hosts=(f"localhost:{server.port}",)
        )
    assert validated.url == url
    assert validated.host == "localhost"


@pytest.mark.parametrize(
    "target",
    [
        "http://127.0.0.2:9/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]:9/",
        "http://10.1.2.3/",
        "http://0177.0.0.1/",
        "file:///etc/passwd",
        "gopher://127.0.0.1:25/",
    ],
)
async def test_redirect_to_a_forbidden_target_is_blocked(target: str) -> None:
    with RedirectServer(target) as server, pytest.raises(UrlBlocked):
        await preflight_redirects(
            f"http://localhost:{server.port}/",
            allowed_local_hosts=(f"localhost:{server.port}",),
        )


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_every_redirect_status_is_followed(status: int) -> None:
    with RedirectServer("http://127.0.0.2:9/", status=status) as server, pytest.raises(UrlBlocked):
        await preflight_redirects(
            f"http://localhost:{server.port}/",
            allowed_local_hosts=(f"localhost:{server.port}",),
        )


async def test_relative_location_is_resolved_against_the_current_hop() -> None:
    with RedirectServer("/next") as server:
        # Every path redirects to /next on the same host, which is allowed, so
        # the chain simply runs out of hops without a verdict.
        await preflight_redirects(
            f"http://localhost:{server.port}/start",
            allowed_local_hosts=(f"localhost:{server.port}",),
            max_hops=3,
        )
    assert [path for path, _ in server.hits] == ["/start", "/next", "/next"]


async def test_connection_is_pinned_to_the_validated_address(monkeypatch) -> None:
    """DNS rebinding: a second lookup that answers differently must not matter.

    The pre-flight validates `localhost` (resolving it once), then connects to
    that address itself. If it resolved again at connect time, this fake
    resolver would send it to a closed port and the redirect would go unseen.
    """
    with RedirectServer("http://127.0.0.2:9/") as server:
        real = socket.getaddrinfo
        calls: list[str] = []

        def rebinding(host, port, *args, **kwargs):
            # anyio passes the name as IDNA bytes; the SSRF guard as str.
            name = host.decode() if isinstance(host, bytes) else host
            if name == "localhost":
                calls.append(name)
                if len(calls) > 1:
                    return real("127.0.0.1", 1, *args, **kwargs)
            return real(host, port, *args, **kwargs)

        monkeypatch.setattr(socket, "getaddrinfo", rebinding)
        with pytest.raises(UrlBlocked):
            await preflight_redirects(
                f"http://localhost:{server.port}/",
                allowed_local_hosts=(f"localhost:{server.port}",),
            )
    assert calls == ["localhost"], "resolved once, by the guard, never again to connect"
    assert server.hits and server.hits[0][1] == f"localhost:{server.port}", "Host header kept"


async def test_unreachable_host_is_not_a_verdict() -> None:
    validated = await preflight_redirects(
        "http://localhost:9/", allowed_local_hosts=("localhost:9",), timeout_s=2
    )
    assert validated.port == 9


async def test_the_submitted_url_itself_is_still_validated() -> None:
    with pytest.raises(UrlBlocked):
        await preflight_redirects("http://127.0.0.1:22/")
