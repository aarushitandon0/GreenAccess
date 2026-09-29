"""Egress proxy that enforces the SSRF policy on every browser connection.

Single responsibility: stand between the scan browser and the network and
refuse any connection :func:`app.security.ssrf.validate_url` would refuse.

Why this exists
---------------
The Playwright route guard (:func:`app.security.ssrf.make_request_guard`) sees
every request the page *starts*, but Playwright never routes **redirect hops**:
if an allowed URL answers ``302 Location: http://169.254.169.254/``, Chromium
follows it without calling the route handler (Playwright documents that route
overrides do not apply to redirected requests). A guard that only sees the
first hop is not a guard.

This proxy sees every connection instead: the first hop, every redirect hop,
subresources, iframes, workers and WebSocket tunnels alike. For each one it

1. validates the target with the same ``validate_url`` (scheme, allow-list,
   every resolved A/AAAA record against the blocked ranges), and
2. connects to the **exact IP address it just validated**, never re-resolving,
   so a DNS answer that changes between check and connect (DNS rebinding)
   cannot redirect the connection.

It speaks the two forms a browser uses with an HTTP proxy:

* ``CONNECT host:port`` for HTTPS and WebSockets: validated, then tunnelled.
  TLS stays end-to-end between the browser and the site.
* ``GET http://host/path`` (absolute-form) for plain HTTP: validated, rewritten
  to origin-form with ``Connection: close``, forwarded, and the response piped
  back unchanged. One request per client connection keeps the framing trivial.

Response bytes pass through untouched, so the CDP transfer sizes the carbon
model reads are unaffected.

It listens on 127.0.0.1 on an ephemeral port and lives exactly as long as one
scan's browser.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.security.ssrf import UrlBlocked, ValidatedUrl, validate_url

logger = logging.getLogger(__name__)

MAX_HEAD_BYTES = 64 * 1024
HEAD_TIMEOUT_S = 30.0
CONNECT_TIMEOUT_S = 10.0
PIPE_CHUNK = 64 * 1024

# Hop-by-hop headers the proxy must not forward (RFC 9110 §7.6.1), plus the
# proxy-specific ones a browser adds.
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-connection",
        "proxy-authorization",
        "proxy-authenticate",
        "te",
        "trailer",
        "upgrade",
    }
)

#: Set on a 403 the proxy generated because the SSRF policy refused a target.
BLOCKED_HEADER = "X-GreenAccess-Blocked"
#: Set on any other response the proxy generated itself (400, 502), so the
#: scanner never mistakes the proxy's own error page for the site's page.
PROXY_ERROR_HEADER = "X-GreenAccess-Proxy-Error"


@dataclass
class EgressProxy:
    """A per-scan SSRF-enforcing HTTP proxy.

    Use as an async context manager; :attr:`url` is what the browser is
    launched with.
    """

    allowed_local_hosts: tuple[str, ...] = ()
    on_block: Callable[[str, str], None] | None = None
    #: ``(target, reason)`` for every refused connection. ``target`` is the URL
    #: for plain HTTP, ``host:port`` for a CONNECT tunnel.
    blocked: list[tuple[str, str]] = field(default_factory=list)

    _server: asyncio.Server | None = None
    _tasks: set[asyncio.Task[None]] = field(default_factory=set)

    @property
    def port(self) -> int:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("EgressProxy is not running")
        return int(self._server.sockets[0].getsockname()[1])

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def __aenter__(self) -> EgressProxy:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(OSError):
                await self._server.wait_closed()
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, OSError):
                await task

    # -- policy ----------------------------------------------------------- #

    def blocked_hosts(self) -> set[str]:
        """``host:port`` of every refused target, for matching a navigation."""
        hosts: set[str] = set()
        for target, _reason in self.blocked:
            hosts.add(host_port(target))
        return hosts

    def _record(self, target: str, reason: str) -> None:
        logger.info("egress proxy blocked %s: %s", target, reason)
        self.blocked.append((target, reason))
        if self.on_block is not None:
            self.on_block(target, reason)

    async def _validate(self, url: str) -> ValidatedUrl:
        # getaddrinfo blocks; keep it off the event loop.
        return await asyncio.to_thread(
            validate_url, url, allowed_local_hosts=self.allowed_local_hosts
        )

    # -- connection handling ---------------------------------------------- #

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        try:
            await self._serve_one(reader, writer)
        except (
            OSError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            TimeoutError,
            ValueError,
        ):
            # A malformed request or a dropped connection ends this one
            # connection; the proxy keeps serving the others.
            pass
        finally:
            with contextlib.suppress(OSError, RuntimeError):
                writer.close()
            if task is not None:
                self._tasks.discard(task)

    async def _serve_one(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await asyncio.wait_for(_read_head(reader), timeout=HEAD_TIMEOUT_S)
        if head is None:
            return
        request_line, headers = head
        parts = request_line.split(" ")
        if len(parts) != 3:
            await _reply(writer, 400, "Bad Request")
            return
        method, target, version = parts

        if method.upper() == "CONNECT":
            await self._tunnel(target, reader, writer)
        else:
            await self._forward(method, target, version, headers, reader, writer)

    async def _tunnel(
        self, target: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        host, _, port_text = target.rpartition(":")
        if not host or not port_text.isdigit():
            await _reply(writer, 400, "Bad Request")
            return
        # The scheme only matters for the default port, and CONNECT always
        # names one; https is the honest description of a tunnel.
        try:
            validated = await self._validate(f"https://{host}:{port_text}/")
        except UrlBlocked as exc:
            self._record(target, exc.reason)
            await _reply(writer, 403, "Forbidden", exc.reason)
            return

        upstream = await _open(validated)
        if upstream is None:
            await _reply(writer, 502, "Bad Gateway")
            return
        up_reader, up_writer = upstream
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        await _pipe_both(reader, writer, up_reader, up_writer)

    async def _forward(
        self,
        method: str,
        target: str,
        version: str,
        headers: list[tuple[str, str]],
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        parts = urlsplit(target)
        if parts.scheme.lower() != "http" or not parts.hostname:
            # Browsers only send absolute-form http:// URLs to an HTTP proxy.
            await _reply(writer, 400, "Bad Request")
            return
        try:
            validated = await self._validate(target)
        except UrlBlocked as exc:
            self._record(target, exc.reason)
            await _reply(writer, 403, "Forbidden", exc.reason)
            return

        upstream = await _open(validated)
        if upstream is None:
            await _reply(writer, 502, "Bad Gateway", f"could not connect to {validated.host}")
            return
        up_reader, up_writer = upstream

        origin_form = parts.path or "/"
        if parts.query:
            origin_form += "?" + parts.query
        lines = [f"{method} {origin_form} {version}"]
        lines += [f"{name}: {value}" for name, value in headers if name.lower() not in _HOP_BY_HOP]
        lines.append("Connection: close")
        up_writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        await up_writer.drain()
        await _pipe_both(reader, writer, up_reader, up_writer)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def host_port(target: str) -> str:
    """``host:port`` for a URL or an already-bare ``host:port``."""
    if "://" not in target:
        return target.lower()
    parts = urlsplit(target)
    try:
        port = parts.port
    except ValueError:
        port = None
    port = port or (443 if parts.scheme.lower() in {"https", "wss"} else 80)
    return f"{(parts.hostname or '').lower()}:{port}"


async def _read_head(reader: asyncio.StreamReader) -> tuple[str, list[tuple[str, str]]] | None:
    try:
        raw = await reader.readuntil(b"\r\n\r\n")
    except asyncio.IncompleteReadError:
        return None
    if len(raw) > MAX_HEAD_BYTES:
        return None
    text = raw.decode("latin-1")
    lines = text.split("\r\n")
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        if not line:
            continue
        name, sep, value = line.partition(":")
        if sep:
            headers.append((name.strip(), value.strip()))
    return lines[0], headers


async def _reply(writer: asyncio.StreamWriter, status: int, reason: str, detail: str = "") -> None:
    body = (detail or reason).encode("utf-8")
    marker = BLOCKED_HEADER if status == 403 else PROXY_ERROR_HEADER
    # A header value must be one line; keep it ASCII so an IDN host in the
    # reason cannot break the encoding below.
    value = (detail or reason).replace("\r", " ").replace("\n", " ")
    value = value.encode("ascii", "replace").decode("ascii")
    writer.write(
        (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"{marker}: {value}\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("latin-1")
        + body
    )
    with contextlib.suppress(OSError):
        await writer.drain()


async def _open(
    validated: ValidatedUrl,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter] | None:
    """Connect to one of the addresses that were validated. Never re-resolves."""
    for address in validated.resolved_ips:
        try:
            return await asyncio.wait_for(
                asyncio.open_connection(address, validated.port), timeout=CONNECT_TIMEOUT_S
            )
        except (OSError, TimeoutError) as exc:
            logger.info("egress proxy could not reach %s:%s: %s", address, validated.port, exc)
    return None


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(PIPE_CHUNK)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError, RuntimeError):
            if writer.can_write_eof():
                writer.write_eof()


async def _pipe_both(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    up_reader: asyncio.StreamReader,
    up_writer: asyncio.StreamWriter,
) -> None:
    """Relay both directions until either side finishes, then close both.

    Upstream EOF means everything has been relayed (each write is drained);
    client EOF means the browser abandoned the connection.
    """
    tasks = {
        asyncio.create_task(_pipe(client_reader, up_writer)),
        asyncio.create_task(_pipe(up_reader, client_writer)),
    }
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, OSError):
                await task
        with contextlib.suppress(OSError, RuntimeError):
            up_writer.close()
