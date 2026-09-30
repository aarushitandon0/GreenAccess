"""Pre-flight redirect check for a scan request (CLAUDE.md security rules).

Single responsibility: follow a user-supplied URL's redirect chain hop by hop,
without a browser, and raise :class:`~app.security.ssrf.UrlBlocked` if any hop
lands somewhere the SSRF guard forbids.

Why this exists alongside the browser's own guards: ``POST /api/scans`` should
answer ``URL_BLOCKED`` synchronously for ``https://public.example/`` that 302s
to ``http://169.254.169.254/``, instead of accepting the scan and failing it
later over SSE. The browser still re-validates every hop at scan time
(:mod:`app.scanner.browser`), because a server can answer this pre-flight
differently from the real visit. This check is the early, friendly answer; the
browser guard is the enforcement.

Each hop connects to the exact address :func:`~app.security.ssrf.validate_url`
just approved, with the original ``Host`` header and TLS server name, so a DNS
answer that changes between the check and the connection (DNS rebinding) cannot
redirect the request. Proxies from the environment are ignored for the same
reason: a proxy would do its own resolution.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Final
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app.security.ssrf import ValidatedUrl, validate_url

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_REDIRECT_HOPS",
    "PREFLIGHT_TIMEOUT_S",
    "pinned_request",
    "pinned_send",
    "preflight_redirects",
]

#: Hops followed before the pre-flight stops looking. Anything longer is left
#: to the browser guard, which checks every hop however long the chain.
MAX_REDIRECT_HOPS: Final[int] = 10

#: Per-hop budget. A slow site is not a blocked site; it is left to the scan.
PREFLIGHT_TIMEOUT_S: Final[float] = 5.0

_REDIRECT_STATUSES: Final[frozenset[int]] = frozenset({301, 302, 303, 307, 308})

_USER_AGENT: Final[str] = "GreenAccessBot/0.1 (redirect pre-flight)"


def pinned_request(
    hop: ValidatedUrl, timeout_s: float, address: str | None = None
) -> httpx.Request:
    """A GET for `hop` that connects to `address`, not to a fresh lookup.

    `address` must be one of `hop.resolved_ips`; it defaults to the first.
    Prefer :func:`pinned_send`, which tries each of them in turn.

    Public so the patcher's asset fetcher (:mod:`app.security.fetch`) pins its
    connections the same way this pre-flight does.
    """
    parts = urlsplit(hop.url)
    headers = {"Host": parts.netloc, "User-Agent": _USER_AGENT}
    extensions: dict[str, object] = {"timeout": httpx.Timeout(timeout_s).as_dict()}
    if address is None and not hop.resolved_ips:
        # Allow-listed dev host that did not resolve; nothing to pin to.
        return httpx.Request("GET", hop.url, headers=headers, extensions=extensions)

    address = address if address is not None else hop.resolved_ips[0]
    literal = f"[{address}]" if ":" in address else address
    pinned = urlunsplit((parts.scheme, f"{literal}:{hop.port}", parts.path or "/", parts.query, ""))
    if hop.scheme == "https":
        extensions["sni_hostname"] = hop.host
    return httpx.Request("GET", pinned, headers=headers, extensions=extensions)


async def pinned_send(
    transport: httpx.AsyncBaseTransport, hop: ValidatedUrl, timeout_s: float
) -> httpx.Response:
    """GET `hop`, trying each address the guard validated until one answers.

    A name can resolve to several addresses and only some of them be
    reachable: a dual-stack host whose AAAA record answers first on a machine
    with no IPv6 route is the everyday case, and `localhost` on Windows is
    ``::1`` before ``127.0.0.1``. Stopping at the first address would turn that
    into a connection error, which the callers read as "no verdict" -- the
    redirect pre-flight would stop looking and the patcher would lose an asset.

    Every candidate is an address `validate_url` already approved, so trying
    the next one adds no reach; the connection is still never re-resolved.
    Raises the last :class:`httpx.HTTPError` if none of them connect.
    """
    candidates: tuple[str | None, ...] = hop.resolved_ips or (None,)
    last: httpx.HTTPError | None = None
    for address in candidates:
        try:
            return await transport.handle_async_request(pinned_request(hop, timeout_s, address))
        except httpx.HTTPError as exc:
            last = exc
            logger.debug("pinned request to %s for %s failed: %s", address, hop.url, exc)
    assert last is not None  # candidates is never empty, so the loop either returned or set this
    raise last


async def _next_location(
    transport: httpx.AsyncBaseTransport, hop: ValidatedUrl, timeout_s: float
) -> str | None:
    """The raw redirect target of `hop`, or None if it does not redirect.

    Sent straight through the transport rather than a client: a client parses
    ``Location`` itself and raises on forms like ``http://0177.0.0.1/``, which
    would hide exactly the obfuscated addresses this check exists to catch.
    The raw header goes to :func:`~app.security.ssrf.validate_url` instead.
    """
    response = await pinned_send(transport, hop, timeout_s)
    try:
        if response.status_code in _REDIRECT_STATUSES:
            return response.headers.get("location")
        return None
    finally:
        # Only the status line and headers matter; never download the body.
        await response.aclose()


async def preflight_redirects(
    url: str,
    *,
    allowed_local_hosts: tuple[str, ...] | frozenset[str] = (),
    max_hops: int = MAX_REDIRECT_HOPS,
    timeout_s: float = PREFLIGHT_TIMEOUT_S,
) -> ValidatedUrl:
    """Validate `url` and every redirect it leads to. Raises ``UrlBlocked``.

    Returns the validation of the URL as submitted. A network failure part way
    through is not a verdict: the chain so far was safe, and the scan itself
    will report the failure (``NAV_FAILED`` or ``TIMEOUT``) over SSE.
    """
    first = await asyncio.to_thread(validate_url, url, allowed_local_hosts=allowed_local_hosts)
    current = first

    # A bare transport: no redirect handling, no cookies, and no proxy from
    # the environment (a proxy would do its own DNS resolution).
    async with httpx.AsyncHTTPTransport(retries=0) as transport:
        for _ in range(max_hops):
            try:
                location = await _next_location(transport, current, timeout_s)
            except httpx.HTTPError as exc:
                logger.info("redirect pre-flight stopped at %s: %s", current.url, exc)
                break
            if not location:
                break
            # Raises UrlBlocked for a forbidden hop, which is the whole point.
            current = await asyncio.to_thread(
                validate_url,
                urljoin(current.url, location),
                allowed_local_hosts=allowed_local_hosts,
            )

    return first
