"""CDP network collector (MASTERSPEC §6.2).

Records every request the page makes via Chrome DevTools Protocol ``Network.*``
events and aggregates them into the byte counts the carbon module needs.

The collector is deliberately split from Playwright: :meth:`NetworkCollector.handle`
takes a CDP method name and its raw parameter dict, so the aggregation can be
unit-tested against recorded event fixtures with no browser involved
(MASTERSPEC §15).

Bytes
-----
``total_bytes`` sums ``encodedDataLength`` from ``Network.loadingFinished``,
which is **transfer size**: what actually crossed the wire, after compression,
including response headers. That is the figure the Sustainable Web Design model
expects.

``decoded_bytes`` sums ``dataLength`` from ``Network.dataReceived``, which is
the size after decompression. The gap between the two is what the
``uncompressed_text`` detector reads.

Third-party classification
--------------------------
MASTERSPEC §6.2 defines third party as "registrable domain differs from the
page's" and asks us to document the choice. We use ``tldextract`` in offline
mode, so no suffix list is fetched at scan time.

There is one documented departure. A registrable domain does not exist for
``localhost``, bare IP addresses, or single-label intranet hostnames —
``tldextract`` returns an empty string for all of them, which would make
``localhost:8081`` and ``localhost:8082`` the same party. For those hosts we
compare ``host:port`` instead, because in that setting the port is what
separates one origin from another. This is what makes the demo site's tracker
host (MASTERSPEC §11 puts it on ``localhost:8082``) classify as third party,
and it matches how a developer would describe it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import tldextract

from app.models import (
    FontSummary,
    ResourceType,
    ThirdPartySummary,
    UncompressedResource,
)

logger = logging.getLogger(__name__)

# Offline: never fetch the public suffix list mid-scan. Falls back to the
# snapshot bundled with tldextract.
_extract = tldextract.TLDExtract(suffix_list_urls=())


def _top_domain(host: str) -> str:
    """The registrable domain for `host`, or "" if it has none.

    tldextract 5.3 renamed ``registered_domain`` to
    ``top_domain_under_public_suffix``; both spellings are handled so a version
    bump in either direction does not break the scan.
    """
    parsed = _extract(host)
    value = getattr(parsed, "top_domain_under_public_suffix", None)
    if value is None:
        value = parsed.registered_domain
    return value or ""


# MASTERSPEC §6.2 type mapping from the CDP resourceType.
_TYPE_MAP: dict[str, ResourceType] = {
    "Document": ResourceType.HTML,
    "Stylesheet": ResourceType.CSS,
    "Script": ResourceType.JS,
    "Image": ResourceType.IMG,
    "Font": ResourceType.FONT,
    "Media": ResourceType.MEDIA,
}

# Types the uncompressed_text detector considers (MASTERSPEC §7.3).
TEXT_TYPES = frozenset({ResourceType.HTML, ResourceType.CSS, ResourceType.JS})

# MASTERSPEC §7.3: only flag text resources above this decoded size.
UNCOMPRESSED_MIN_BYTES = 2_048

# MASTERSPEC §7.3: a compressed text resource typically sheds about 70%.
COMPRESSION_SAVING_RATIO = 0.70

_COMPRESSED_ENCODINGS = frozenset({"gzip", "br", "deflate", "zstd", "compress"})


def resource_type_from_cdp(cdp_type: str | None) -> ResourceType:
    """Map a CDP ``resourceType`` to our bucket, defaulting to OTHER."""
    if not cdp_type:
        return ResourceType.OTHER
    return _TYPE_MAP.get(cdp_type, ResourceType.OTHER)


def registrable_domain(url: str) -> str:
    """The registrable domain for `url`, or "" when it has none."""
    host = urlparse(url).hostname or ""
    if not host:
        return ""
    return _top_domain(host)


def party_key(url: str) -> str:
    """A stable identity for "which party served this".

    The registrable domain when there is one; otherwise ``host:port``. See the
    module docstring for why the fallback exists.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return ""

    domain = _top_domain(host)
    if domain:
        return domain

    # No registrable domain: localhost, an IP literal, or a single-label name.
    scheme = (parsed.scheme or "http").lower()
    port = parsed.port or (443 if scheme == "https" else 80)
    return f"{host}:{port}"


def is_third_party(resource_url: str, page_url: str) -> bool:
    """True when `resource_url` is served by a different party than `page_url`."""
    resource_key = party_key(resource_url)
    page_key = party_key(page_url)
    if not resource_key or not page_key:
        return False
    return resource_key != page_key


@dataclass
class RequestRecord:
    """Everything we learned about one request."""

    request_id: str
    url: str = ""
    method: str = "GET"
    resource_type: ResourceType = ResourceType.OTHER
    initiator_type: str = ""
    initiator_url: str = ""

    status: int = 0
    mime_type: str = ""
    response_headers: dict[str, str] = field(default_factory=dict)

    #: Transfer size, from loadingFinished. What crossed the wire.
    transfer_bytes: int = 0
    #: Decoded size, summed from dataReceived.
    decoded_bytes: int = 0
    #: Encoded bytes seen so far via dataReceived, before loadingFinished.
    #: Only used for the live page-weight cap; totals use transfer_bytes.
    streamed_bytes: int = 0

    finished: bool = False
    failed: bool = False
    failure_text: str = ""
    from_cache: bool = False
    #: Requests that ended in a 3xx and were followed.
    redirected_to: str = ""

    @property
    def content_encoding(self) -> str:
        for name, value in self.response_headers.items():
            if name.lower() == "content-encoding":
                return value.strip().lower()
        return ""

    @property
    def is_compressed(self) -> bool:
        encoding = self.content_encoding
        return any(token.strip() in _COMPRESSED_ENCODINGS for token in encoding.split(","))

    @property
    def effective_decoded_bytes(self) -> int:
        """Decoded size, falling back to transfer size when nothing was recorded."""
        if self.decoded_bytes:
            return self.decoded_bytes
        return self.transfer_bytes


@dataclass
class NetworkSummary:
    """Aggregated view of a page load."""

    total_bytes: int = 0
    request_count: int = 0
    by_type: dict[ResourceType, int] = field(default_factory=dict)
    third_party: ThirdPartySummary = field(default_factory=ThirdPartySummary)
    fonts: FontSummary = field(default_factory=FontSummary)
    uncompressed_text: list[UncompressedResource] = field(default_factory=list)
    records: list[RequestRecord] = field(default_factory=list)
    failed_count: int = 0


class NetworkCollector:
    """Accumulates CDP ``Network.*`` events for one page load.

    Feed it events with :meth:`handle`; read the result with :meth:`summarize`.
    """

    def __init__(self, page_url: str = "") -> None:
        self.page_url = page_url
        self._records: dict[str, RequestRecord] = {}
        #: Redirect hops, which reuse the same requestId and would otherwise
        #: overwrite each other.
        self._completed_redirects: list[RequestRecord] = []

    # -- ingestion -------------------------------------------------------- #

    def handle(self, method: str, params: dict[str, Any]) -> None:
        """Dispatch one CDP event. Unknown methods are ignored."""
        handler = {
            "Network.requestWillBeSent": self._on_request_will_be_sent,
            "Network.responseReceived": self._on_response_received,
            "Network.dataReceived": self._on_data_received,
            "Network.loadingFinished": self._on_loading_finished,
            "Network.loadingFailed": self._on_loading_failed,
            "Network.requestServedFromCache": self._on_served_from_cache,
        }.get(method)
        if handler is None:
            return
        try:
            handler(params)
        except (AttributeError, KeyError, TypeError, ValueError):
            logger.warning("malformed %s event", method, exc_info=True)

    def _record(self, request_id: str) -> RequestRecord:
        record = self._records.get(request_id)
        if record is None:
            record = RequestRecord(request_id=request_id)
            self._records[request_id] = record
        return record

    def _on_request_will_be_sent(self, params: dict[str, Any]) -> None:
        request_id = params.get("requestId", "")
        request = params.get("request") or {}

        # A redirect reuses the requestId. Bank the previous hop before the
        # record is overwritten, or its bytes vanish from the total.
        redirect_response = params.get("redirectResponse")
        if redirect_response and request_id in self._records:
            previous = self._records[request_id]
            previous.status = int(redirect_response.get("status") or 0)
            previous.redirected_to = request.get("url", "")
            previous.finished = True
            encoded = redirect_response.get("encodedDataLength")
            if encoded:
                previous.transfer_bytes = int(encoded)
            self._completed_redirects.append(previous)
            del self._records[request_id]

        record = self._record(request_id)
        record.url = request.get("url", "") or record.url
        record.method = request.get("method", "GET")
        record.resource_type = resource_type_from_cdp(params.get("type"))

        initiator = params.get("initiator") or {}
        record.initiator_type = initiator.get("type", "") or ""
        record.initiator_url = initiator.get("url", "") or ""
        if not record.initiator_url:
            stack = initiator.get("stack") or {}
            frames = stack.get("callFrames") or []
            if frames:
                record.initiator_url = frames[0].get("url", "") or ""

    def _on_response_received(self, params: dict[str, Any]) -> None:
        record = self._record(params.get("requestId", ""))
        response = params.get("response") or {}

        record.url = response.get("url", "") or record.url
        record.status = int(response.get("status") or 0)
        record.mime_type = response.get("mimeType", "") or ""
        record.from_cache = bool(response.get("fromDiskCache")) or record.from_cache

        headers = response.get("headers") or {}
        if isinstance(headers, dict):
            record.response_headers = {str(k): str(v) for k, v in headers.items()}

        # responseReceived carries a resourceType too, and it is more accurate
        # than the one guessed at request time.
        if params.get("type"):
            record.resource_type = resource_type_from_cdp(params.get("type"))

    def _on_data_received(self, params: dict[str, Any]) -> None:
        record = self._record(params.get("requestId", ""))
        record.decoded_bytes += int(params.get("dataLength") or 0)
        record.streamed_bytes += int(params.get("encodedDataLength") or 0)

    def _on_loading_finished(self, params: dict[str, Any]) -> None:
        record = self._record(params.get("requestId", ""))
        encoded = params.get("encodedDataLength")
        if encoded is not None:
            record.transfer_bytes = int(encoded)
        record.finished = True

    def _on_loading_failed(self, params: dict[str, Any]) -> None:
        record = self._record(params.get("requestId", ""))
        record.failed = True
        record.failure_text = params.get("errorText", "") or ""

    def _on_served_from_cache(self, params: dict[str, Any]) -> None:
        self._record(params.get("requestId", "")).from_cache = True

    # -- reading ---------------------------------------------------------- #

    @property
    def records(self) -> list[RequestRecord]:
        """Every request, including completed redirect hops, in arrival order."""
        return [*self._completed_redirects, *self._records.values()]

    @property
    def total_bytes(self) -> int:
        """Live total, so a scan can abort once it exceeds the page-weight cap."""
        return sum(record.transfer_bytes for record in self.records if not record.failed)

    @property
    def live_bytes(self) -> int:
        """Bytes received so far, counting responses still downloading.

        ``total_bytes`` only grows when a response finishes, so one huge
        in-flight download would slip past the page-weight cap until it ended.
        This figure includes what has streamed in so far, from requests that
        later failed or were aborted too, since those bytes still arrived.

        Chromium often reports ``encodedDataLength`` as 0 on ``dataReceived``,
        so an unfinished response falls back to its decoded byte count. That
        can overstate a compressed download, which is the safe direction for a
        safety cap. It is used only for the cap, never for carbon figures.
        """
        return sum(
            record.transfer_bytes
            if record.finished
            else max(record.transfer_bytes, record.streamed_bytes, record.decoded_bytes)
            for record in self.records
        )

    def summarize(self, page_url: str | None = None) -> NetworkSummary:
        """Aggregate everything collected so far."""
        page = page_url or self.page_url
        records = self.records

        summary = NetworkSummary(records=records)
        by_type: dict[ResourceType, int] = {}
        third_party_hosts: list[str] = []
        font_urls: list[str] = []

        for record in records:
            if record.failed:
                summary.failed_count += 1
                continue

            summary.request_count += 1
            summary.total_bytes += record.transfer_bytes
            by_type[record.resource_type] = (
                by_type.get(record.resource_type, 0) + record.transfer_bytes
            )

            if record.url and page and is_third_party(record.url, page):
                summary.third_party.requests += 1
                summary.third_party.bytes += record.transfer_bytes
                if record.resource_type is ResourceType.JS:
                    summary.third_party.script_bytes += record.transfer_bytes
                host = urlparse(record.url).netloc
                if host and host not in third_party_hosts:
                    third_party_hosts.append(host)

            if record.resource_type is ResourceType.FONT:
                summary.fonts.count += 1
                summary.fonts.bytes += record.transfer_bytes
                if record.url:
                    font_urls.append(record.url)

            uncompressed = self._as_uncompressed(record)
            if uncompressed is not None:
                summary.uncompressed_text.append(uncompressed)

        summary.by_type = by_type
        summary.third_party.hosts = third_party_hosts
        summary.fonts.urls = font_urls
        return summary

    @staticmethod
    def _as_uncompressed(record: RequestRecord) -> UncompressedResource | None:
        """Return an UncompressedResource if `record` is uncompressed text."""
        if record.resource_type not in TEXT_TYPES:
            return None
        if record.is_compressed:
            return None
        decoded = record.effective_decoded_bytes
        if decoded <= UNCOMPRESSED_MIN_BYTES:
            return None
        return UncompressedResource(
            url=record.url,
            resource_type=record.resource_type,
            transfer_bytes=record.transfer_bytes,
            decoded_bytes=decoded,
            content_encoding=record.content_encoding,
            estimated_saving_bytes=int(decoded * COMPRESSION_SAVING_RATIO),
        )
