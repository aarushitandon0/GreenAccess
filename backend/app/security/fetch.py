"""SSRF-safe HTTP fetches for the patcher (CLAUDE.md security rules).

Single responsibility: download one URL's body without ever connecting to an
address the SSRF guard forbids, and without letting a hostile server make us
download without limit.

Every hop, the first and each redirect, goes through
:func:`~app.security.ssrf.validate_url` and is sent to the exact address that
validation approved (:func:`~app.security.redirects.pinned_request`), so DNS
rebinding between check and connect cannot redirect it. Proxies from the
environment are ignored for the same reason. Bodies are streamed and cut off
at a per-file cap, and a fetcher refuses to exceed its total budget
(``MAX_PAGE_BYTES`` by default, MASTERSPEC §6.1).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Final
from urllib.parse import urljoin

import httpx

from app.security.redirects import pinned_send
from app.security.ssrf import UrlBlocked, validate_url

logger = logging.getLogger(__name__)

__all__ = ["FetchError", "Fetched", "SafeFetcher"]

MAX_REDIRECTS: Final[int] = 5
DEFAULT_TIMEOUT_S: Final[float] = 15.0
DEFAULT_MAX_FILE_BYTES: Final[int] = 15 * 1024 * 1024
_REDIRECTS: Final[frozenset[int]] = frozenset({301, 302, 303, 307, 308})


class FetchError(Exception):
    """A fetch failed. ``blocked`` is True when the SSRF guard refused it."""

    def __init__(self, message: str, *, blocked: bool = False) -> None:
        self.blocked = blocked
        super().__init__(message)


@dataclass(frozen=True)
class Fetched:
    url: str
    final_url: str
    status: int
    content_type: str
    body: bytes


@dataclass
class SafeFetcher:
    """Validated, pinned, capped GETs, memoised by URL for one patch job."""

    allowed_local_hosts: tuple[str, ...] = ()
    max_total_bytes: int = 26_214_400
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    timeout_s: float = DEFAULT_TIMEOUT_S
    fetched_bytes: int = 0
    _memo: dict[str, Fetched] = field(default_factory=dict, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def get(self, url: str) -> Fetched:
        """GET `url` (following up to 5 validated redirects); raises FetchError."""
        if url in self._memo:
            return self._memo[url]
        async with self._lock:
            if url in self._memo:
                return self._memo[url]
            fetched = await self._get(url)
            self._memo[url] = fetched
            return fetched

    async def _get(self, url: str) -> Fetched:
        current = url
        async with httpx.AsyncHTTPTransport(retries=0) as transport:
            for _ in range(MAX_REDIRECTS + 1):
                try:
                    hop = await asyncio.to_thread(
                        validate_url, current, allowed_local_hosts=self.allowed_local_hosts
                    )
                except UrlBlocked as blocked:
                    raise FetchError(
                        f"{current} is blocked: {blocked.reason}", blocked=True
                    ) from blocked
                try:
                    response = await pinned_send(transport, hop, self.timeout_s)
                except httpx.HTTPError as exc:
                    raise FetchError(f"could not fetch {current}: {exc}") from exc
                try:
                    if response.status_code in _REDIRECTS:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchError(f"{current} redirected without a Location")
                        current = urljoin(current, location)
                        continue
                    body = await self._read_capped(response, current)
                    return Fetched(
                        url=url,
                        final_url=current,
                        status=response.status_code,
                        content_type=response.headers.get("content-type", ""),
                        body=body,
                    )
                finally:
                    await response.aclose()
        raise FetchError(f"{url} redirected more than {MAX_REDIRECTS} times")

    async def _read_capped(self, response: httpx.Response, url: str) -> bytes:
        chunks: list[bytes] = []
        size = 0
        try:
            # Decoded bytes are what land on disk, so the caps apply to them;
            # that also defuses a compressed "zip bomb" response.
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > self.max_file_bytes:
                    raise FetchError(f"{url} is larger than {self.max_file_bytes:,} bytes")
                if self.fetched_bytes + size > self.max_total_bytes:
                    raise FetchError(
                        f"fetching {url} would exceed the {self.max_total_bytes:,}-byte budget"
                    )
                chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise FetchError(f"could not read {url}: {exc}") from exc
        self.fetched_bytes += size
        return b"".join(chunks)
