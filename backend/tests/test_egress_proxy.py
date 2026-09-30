"""Egress proxy tests (CLAUDE.md security rules; MASTERSPEC §15 "redirects").

Speaks raw proxy protocol to the proxy over a socket, the way Chromium does,
so no browser is needed. Browser-level redirect tests live in test_browser.py.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Callable

import pytest

from app.security import egress_proxy
from app.security.egress_proxy import BLOCKED_HEADER, PROXY_ERROR_HEADER, EgressProxy, host_port

HTML = {"Content-Type": "text/html"}


async def _exchange(proxy: EgressProxy, request: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    writer.write(request)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(-1), timeout=10)
    writer.close()
    return data


def _allow(base: str) -> tuple[str, ...]:
    return (base.removeprefix("http://"),)


async def test_forwards_an_allowed_http_request(local_site: Callable):
    base = local_site({"/page?x=1": (200, HTML, b"<p>hello</p>")})
    async with EgressProxy(allowed_local_hosts=_allow(base)) as proxy:
        reply = await _exchange(
            proxy,
            f"GET {base}/page?x=1 HTTP/1.1\r\nHost: {base[7:]}\r\n"
            "Proxy-Connection: keep-alive\r\n\r\n".encode(),
        )
    assert reply.startswith(b"HTTP/1.1 200")
    assert reply.endswith(b"<p>hello</p>")
    assert proxy.blocked == []


@pytest.mark.parametrize(
    "target",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:1/",
        "http://10.0.0.5/admin",
        "http://[::1]/",
        "http://0x7f000001/",
    ],
)
async def test_refuses_a_blocked_http_target(target: str):
    async with EgressProxy() as proxy:
        reply = await _exchange(proxy, f"GET {target} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    assert reply.startswith(b"HTTP/1.1 403")
    assert BLOCKED_HEADER.encode() in reply
    assert [t for t, _ in proxy.blocked] == [target]


@pytest.mark.parametrize(
    "target", ["169.254.169.254:443", "127.0.0.1:22", "192.168.1.1:443", "localhost:6379"]
)
async def test_refuses_a_blocked_connect_tunnel(target: str):
    async with EgressProxy() as proxy:
        reply = await _exchange(
            proxy, f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode()
        )
    assert reply.startswith(b"HTTP/1.1 403")
    assert proxy.blocked[0][0] == target


async def test_tunnels_an_allowed_connect(local_site: Callable):
    base = local_site({"/": (200, HTML, b"tunnelled")})
    host = base.removeprefix("http://")
    async with EgressProxy(allowed_local_hosts=(host,)) as proxy:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(f"CONNECT {host} HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
        await writer.drain()
        established = await reader.readuntil(b"\r\n\r\n")
        writer.write(f"GET / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
        await writer.drain()
        body = await asyncio.wait_for(reader.read(-1), timeout=10)
        writer.close()
    assert established.startswith(b"HTTP/1.1 200")
    assert body.endswith(b"tunnelled")


async def test_rejects_non_http_absolute_urls():
    async with EgressProxy() as proxy:
        reply = await _exchange(proxy, b"GET ftp://example.com/ HTTP/1.1\r\n\r\n")
    assert reply.startswith(b"HTTP/1.1 400")


async def test_connects_to_the_validated_address_without_re_resolving(
    monkeypatch: pytest.MonkeyPatch,
):
    """DNS rebinding: the name is resolved once, checked, and that IP is used.

    A resolver that answers a public address first and 127.0.0.1 afterwards
    must not get a second chance to answer.
    """
    answers = iter(["93.184.216.34", "127.0.0.1", "127.0.0.1"])
    lookups: list[str] = []

    def rebinding_getaddrinfo(host, port, *args, **kwargs):
        lookups.append(host)
        address = next(answers)
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port))]

    connected: list[tuple[str, int]] = []

    async def fake_open_connection(host, port):
        connected.append((host, port))
        raise OSError("not really connecting in a unit test")

    monkeypatch.setattr(socket, "getaddrinfo", rebinding_getaddrinfo)
    monkeypatch.setattr(egress_proxy.asyncio, "open_connection", fake_open_connection)

    async with EgressProxy() as proxy:
        # Talk to the proxy with a raw socket: open_connection is patched, and
        # socket.create_connection would consult the patched resolver.
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", proxy.port))
        sock.setblocking(False)
        await loop.sock_sendall(
            sock, b"GET http://rebind.example/ HTTP/1.1\r\nHost: rebind.example\r\n\r\n"
        )
        reply = await asyncio.wait_for(loop.sock_recv(sock, 4096), timeout=10)
        sock.close()

    assert lookups == ["rebind.example"]
    assert connected == [("93.184.216.34", 80)]
    assert reply.startswith(b"HTTP/1.1 502")


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("http://Example.com/a", "example.com:80"),
        ("https://example.com/a", "example.com:443"),
        ("http://127.0.0.1:8081/", "127.0.0.1:8081"),
        ("169.254.169.254:443", "169.254.169.254:443"),
    ],
)
def test_host_port(target: str, expected: str):
    assert host_port(target) == expected


def _closed_port() -> int:
    """A port with nothing listening on it: bound to learn the number, then released.

    Guessing one (a live server's port + 1, say) is not safe: the OS hands out
    ephemeral ports in sequence, so the next number along is very often the
    next socket the test itself binds -- including the proxy's own listener,
    which then answers the forwarded request and decides this test by accident.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


async def test_unreachable_upstream_is_marked_as_a_proxy_error():
    """The scanner must be able to tell the proxy's own 502 from the site's page."""
    async with EgressProxy() as proxy:
        # Chosen once the proxy holds its own port, so it cannot be that port.
        dead = f"127.0.0.1:{_closed_port()}"
        proxy.allowed_local_hosts = (dead,)
        reply = await _exchange(
            proxy, f"GET http://{dead}/ HTTP/1.1\r\nHost: {dead}\r\n\r\n".encode()
        )
    assert reply.startswith(b"HTTP/1.1 502")
    assert PROXY_ERROR_HEADER.encode() in reply
    assert BLOCKED_HEADER.encode() not in reply
    assert proxy.blocked == []


@pytest.mark.parametrize(
    "request_bytes",
    [
        b"GET http://[bad/ HTTP/1.1\r\n\r\n",
        b"GARBAGE\r\n\r\n",
        b"CONNECT nohostport HTTP/1.1\r\n\r\n",
    ],
)
async def test_malformed_requests_do_not_break_the_proxy(
    request_bytes: bytes, local_site: Callable
):
    base = local_site({"/": (200, HTML, b"still alive")})
    async with EgressProxy(allowed_local_hosts=_allow(base)) as proxy:
        await _exchange(proxy, request_bytes)
        reply = await _exchange(proxy, f"GET {base}/ HTTP/1.1\r\nHost: {base[7:]}\r\n\r\n".encode())
    assert reply.endswith(b"still alive")
