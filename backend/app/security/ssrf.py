"""SSRF guard for every user-supplied URL (CLAUDE.md, security rules).

GreenAccess fetches whatever URL a stranger types. Without a guard that is a
server-side request forgery primitive pointed at the host's own network: cloud
metadata endpoints, internal admin panels, databases bound to localhost.

What this module enforces:

* **Scheme allow-list.** Only ``http`` and ``https``. No ``file:``, ``gopher:``,
  ``data:``, ``ftp:`` or anything else.
* **Numeric host forms are decoded before judging them.** ``2130706433``,
  ``0x7f000001``, ``0177.0.0.1`` and ``127.1`` are all 127.0.0.1, and all are
  accepted by browsers and by ``socket``. A guard that only understands dotted
  quads is not a guard.
* **DNS is resolved and every returned record is checked.** A hostname that
  looks innocent can resolve to 127.0.0.1. Checking only the first record leaves
  a hole, so every A and AAAA record must pass.
* **IPv6 embeddings are unwrapped.** ``::ffff:127.0.0.1`` (IPv4-mapped), 6to4,
  Teredo and NAT64 all carry an IPv4 address inside an IPv6 one.
* **Redirects are re-validated.** The first hop being public says nothing about
  the second. :func:`make_request_guard` re-runs validation on every navigation
  and every redirect Playwright reports.

Local development hosts are permitted **only** by naming them in
``ALLOWED_LOCAL_HOSTS`` (MASTERSPEC §17). There is no flag that switches the
guard off.

The resolve-then-connect gap (DNS rebinding) is narrowed but not closed here:
:func:`validate_url` returns the addresses it validated so a caller may pin to
them. See :class:`ValidatedUrl.resolved_ips`.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

__all__ = [
    "UrlBlocked",
    "ValidatedUrl",
    "make_request_guard",
    "parse_ip_literal",
    "validate_url",
]

ALLOWED_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})

# Cloud instance-metadata endpoints. These are link-local or otherwise already
# blocked by range, but they are named explicitly so the failure message says
# what was actually attempted.
METADATA_ADDRESSES: Final[dict[str, str]] = {
    "169.254.169.254": "cloud instance metadata (AWS, Azure, GCP, DigitalOcean)",
    "169.254.170.2": "AWS ECS task metadata",
    "100.100.100.200": "Alibaba Cloud instance metadata",
    "192.0.0.192": "Oracle Cloud instance metadata",
    "fd00:ec2::254": "AWS IPv6 instance metadata",
}

# IPv6 ranges that carry an IPv4 address inside them. Each has to be unwrapped
# and the embedded IPv4 address judged on its own merits.
_NAT64_PREFIX: Final = ipaddress.ip_network("64:ff9b::/96")
_6TO4_PREFIX: Final = ipaddress.ip_network("2002::/16")
_TEREDO_PREFIX: Final = ipaddress.ip_network("2001::/32")

# Extra IPv4 ranges that `is_global` alone does not reject in every Python
# version, listed explicitly so the intent is visible.
_BLOCKED_V4_NETWORKS: Final[tuple[tuple[ipaddress.IPv4Network, str], ...]] = (
    (ipaddress.ip_network("0.0.0.0/8"), "this-network"),
    (ipaddress.ip_network("10.0.0.0/8"), "private (RFC 1918)"),
    (ipaddress.ip_network("100.64.0.0/10"), "carrier-grade NAT (RFC 6598)"),
    (ipaddress.ip_network("127.0.0.0/8"), "loopback"),
    (ipaddress.ip_network("169.254.0.0/16"), "link-local"),
    (ipaddress.ip_network("172.16.0.0/12"), "private (RFC 1918)"),
    (ipaddress.ip_network("192.0.0.0/24"), "IETF protocol assignments"),
    (ipaddress.ip_network("192.0.2.0/24"), "documentation (TEST-NET-1)"),
    (ipaddress.ip_network("192.168.0.0/16"), "private (RFC 1918)"),
    (ipaddress.ip_network("198.18.0.0/15"), "benchmarking"),
    (ipaddress.ip_network("198.51.100.0/24"), "documentation (TEST-NET-2)"),
    (ipaddress.ip_network("203.0.113.0/24"), "documentation (TEST-NET-3)"),
    (ipaddress.ip_network("224.0.0.0/4"), "multicast"),
    (ipaddress.ip_network("240.0.0.0/4"), "reserved"),
)

_BLOCKED_V6_NETWORKS: Final[tuple[tuple[ipaddress.IPv6Network, str], ...]] = (
    (ipaddress.ip_network("::/128"), "unspecified"),
    (ipaddress.ip_network("::1/128"), "loopback"),
    (ipaddress.ip_network("fc00::/7"), "unique local address"),
    (ipaddress.ip_network("fe80::/10"), "link-local"),
    (ipaddress.ip_network("ff00::/8"), "multicast"),
    (ipaddress.ip_network("2001:db8::/32"), "documentation"),
)

_IPV4_DIGITS = re.compile(r"^[0-9]+$")


class UrlBlocked(Exception):
    """A URL was rejected. Maps to the ``URL_BLOCKED`` API error (MASTERSPEC §12)."""

    code: Final[str] = "URL_BLOCKED"

    def __init__(self, reason: str, url: str = "") -> None:
        self.reason = reason
        self.url = url
        super().__init__(reason)

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True)
class ValidatedUrl:
    """A URL that passed every check, plus what it resolved to."""

    url: str
    scheme: str
    host: str
    port: int
    #: Every address the host resolved to. All of them were checked.
    resolved_ips: tuple[str, ...]
    #: True when the host was permitted by ALLOWED_LOCAL_HOSTS rather than by
    #: being publicly routable.
    allowed_by_override: bool = False


# --------------------------------------------------------------------------- #
# Host parsing
# --------------------------------------------------------------------------- #


def parse_ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Return the IP address `host` denotes, in any form, or None if it is a name.

    Handles every numeric form a browser accepts, not just dotted quads:

    >>> parse_ip_literal("127.0.0.1")
    IPv4Address('127.0.0.1')
    >>> parse_ip_literal("2130706433")
    IPv4Address('127.0.0.1')
    >>> parse_ip_literal("0x7f000001")
    IPv4Address('127.0.0.1')
    >>> parse_ip_literal("0177.0.0.1")
    IPv4Address('127.0.0.1')
    >>> parse_ip_literal("127.1")
    IPv4Address('127.0.0.1')
    >>> parse_ip_literal("example.com") is None
    True
    """
    candidate = host.strip()
    if not candidate:
        return None

    # A bracketed IPv6 literal, as it appears in a URL authority.
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]

    # Drop a zone index: fe80::1%eth0.
    if "%" in candidate:
        candidate = candidate.split("%", 1)[0]

    # A trailing dot is a fully-qualified name and is not part of the label.
    candidate = candidate.rstrip(".")
    if not candidate:
        return None

    # Standard textual forms first.
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        pass

    # A bare integer: http://2130706433/ is a valid way to write 127.0.0.1.
    if _IPV4_DIGITS.match(candidate):
        try:
            value = int(candidate, 10)
        except ValueError:
            return None
        if 0 <= value <= 0xFFFFFFFF:
            return ipaddress.IPv4Address(value)
        return None

    # Hexadecimal and octal, whole or per-octet: 0x7f000001, 0177.0.0.1, 127.1.
    lowered = candidate.lower()
    if lowered.startswith("0x"):
        try:
            value = int(lowered, 16)
        except ValueError:
            return None
        if 0 <= value <= 0xFFFFFFFF:
            return ipaddress.IPv4Address(value)
        return None

    # inet_aton understands the remaining legacy forms (a, a.b, a.b.c, a.b.c.d
    # with hex or octal octets). Restrict it to strings that cannot be hostnames
    # so that a name like "0x.example.com" is not mistaken for an address.
    if re.fullmatch(r"[0-9a-fA-FxX.]+", candidate):
        try:
            packed = socket.inet_aton(candidate)
        except OSError:
            return None
        return ipaddress.IPv4Address(packed)

    return None


def _unwrap_embedded_ipv4(
    address: ipaddress.IPv6Address,
) -> tuple[ipaddress.IPv4Address, str] | None:
    """Extract the IPv4 address embedded in an IPv6 one, with a label for why."""
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped, "IPv4-mapped IPv6"
    if address.sixtofour is not None and address in _6TO4_PREFIX:
        return address.sixtofour, "6to4"
    if address.teredo is not None and address in _TEREDO_PREFIX:
        # teredo is (server, client); the client address is the interesting one.
        return address.teredo[1], "Teredo"
    if address in _NAT64_PREFIX:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF), "NAT64"
    return None


def _describe_blocked(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> str | None:
    """Return why `address` must not be fetched, or None if it is acceptable."""
    text = str(address)
    if text in METADATA_ADDRESSES:
        return f"{text} is {METADATA_ADDRESSES[text]}"

    if isinstance(address, ipaddress.IPv6Address):
        embedded = _unwrap_embedded_ipv4(address)
        if embedded is not None:
            inner, kind = embedded
            inner_reason = _describe_blocked(inner)
            if inner_reason is not None:
                return f"{text} is a {kind} address wrapping {inner_reason}"

        for network, label in _BLOCKED_V6_NETWORKS:
            if address in network:
                return f"{text} is {label}"
    else:
        for network, label in _BLOCKED_V4_NETWORKS:
            if address in network:
                return f"{text} is {label}"

    # Backstop for anything the explicit tables miss.
    if not address.is_global:
        return f"{text} is not a globally routable address"

    return None


def _resolve(host: str, port: int) -> tuple[str, ...]:
    """Resolve `host` to every A and AAAA record, or raise UrlBlocked."""
    try:
        records = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UrlBlocked(f"could not resolve host {host!r}: {exc.strerror or exc}") from exc
    except UnicodeError as exc:
        raise UrlBlocked(f"invalid host name {host!r}") from exc

    addresses: list[str] = []
    for record in records:
        sockaddr = record[4]
        address = sockaddr[0]
        if address not in addresses:
            addresses.append(address)

    if not addresses:
        raise UrlBlocked(f"host {host!r} did not resolve to any address")
    return tuple(addresses)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def validate_url(
    raw_url: str,
    *,
    allowed_local_hosts: tuple[str, ...] | frozenset[str] | None = None,
) -> ValidatedUrl:
    """Validate a user-supplied URL, or raise :class:`UrlBlocked`.

    `allowed_local_hosts` comes from the ``ALLOWED_LOCAL_HOSTS`` environment
    variable. A host on that list skips the address checks — that is the only
    way a private address is ever reachable, and it must be set deliberately.
    """
    allow_list = frozenset(h.strip().lower() for h in (allowed_local_hosts or ()) if h.strip())

    if not raw_url or not raw_url.strip():
        raise UrlBlocked("no URL given")

    url = raw_url.strip()

    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise UrlBlocked(f"malformed URL: {exc}", url) from exc

    scheme = parts.scheme.lower()
    if not scheme:
        raise UrlBlocked(
            "URL must start with http:// or https://",
            url,
        )
    if scheme not in ALLOWED_SCHEMES:
        raise UrlBlocked(f"scheme {scheme!r} is not allowed; use http or https", url)

    # Credentials in the authority are a classic way to disguise the real host
    # (http://trusted.example@127.0.0.1/). We do not need them for auditing.
    if parts.username is not None or parts.password is not None:
        raise UrlBlocked("URLs with embedded credentials are not allowed", url)

    try:
        hostname = parts.hostname
    except ValueError as exc:
        raise UrlBlocked(f"malformed host in URL: {exc}", url) from exc

    if not hostname:
        raise UrlBlocked("URL has no host", url)

    host = hostname.lower().rstrip(".")
    if not host:
        raise UrlBlocked("URL has no host", url)

    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise UrlBlocked(f"invalid port: {exc}", url) from exc

    # An explicitly allow-listed development host bypasses the address checks.
    # Both the bare host and host:port forms are accepted on the list.
    if host in allow_list or f"{host}:{port}" in allow_list:
        literal = parse_ip_literal(host)
        resolved = (
            (str(literal),) if literal is not None else _safe_resolve_for_override(host, port)
        )
        return ValidatedUrl(
            url=url,
            scheme=scheme,
            host=host,
            port=port,
            resolved_ips=resolved,
            allowed_by_override=True,
        )

    # A literal address needs no DNS, and must not get any: resolving it would
    # only add a way to be confused.
    literal = parse_ip_literal(host)
    if literal is not None:
        reason = _describe_blocked(literal)
        if reason is not None:
            raise UrlBlocked(f"blocked: {reason}", url)
        return ValidatedUrl(
            url=url,
            scheme=scheme,
            host=host,
            port=port,
            resolved_ips=(str(literal),),
        )

    # A name: resolve it and check every address it offers.
    addresses = _resolve(host, port)
    for address_text in addresses:
        try:
            address = ipaddress.ip_address(address_text)
        except ValueError as exc:  # pragma: no cover - getaddrinfo returns valid text
            raise UrlBlocked(f"unparseable address {address_text!r} for {host}", url) from exc
        reason = _describe_blocked(address)
        if reason is not None:
            raise UrlBlocked(f"blocked: {host} resolves to {reason}", url)

    return ValidatedUrl(
        url=url,
        scheme=scheme,
        host=host,
        port=port,
        resolved_ips=addresses,
    )


def _safe_resolve_for_override(host: str, port: int) -> tuple[str, ...]:
    """Resolve an allow-listed host, tolerating failure.

    An allow-listed host is trusted by configuration, so a resolution failure
    here is reported as an empty tuple rather than blocking the scan.
    """
    try:
        return _resolve(host, port)
    except UrlBlocked:
        return ()


def make_request_guard(
    *,
    allowed_local_hosts: tuple[str, ...] | frozenset[str] | None = None,
    on_block: Callable[[str, str], None] | None = None,
) -> Callable[..., object]:
    """Build a Playwright route handler that re-validates every request.

    MASTERSPEC §4 validates the URL once before the browser starts. That is not
    enough on its own: the page can redirect, and can issue subresource requests
    to anywhere. This handler runs on every request the context makes, so a
    302 to ``http://169.254.169.254/`` is aborted rather than followed.

    Usage::

        await context.route("**/*", make_request_guard(
            allowed_local_hosts=settings.allowed_local_hosts,
        ))

    `on_block` is called with ``(url, reason)`` for each blocked request, so the
    caller can record what was attempted.
    """

    async def guard(route, request) -> None:  # noqa: ANN001 - Playwright types
        try:
            validate_url(request.url, allowed_local_hosts=allowed_local_hosts)
        except UrlBlocked as blocked:
            if on_block is not None:
                on_block(request.url, blocked.reason)
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    return guard
