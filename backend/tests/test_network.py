"""CDP network aggregation tests (MASTERSPEC §6.2, §15).

Driven by recorded CDP event sequences, so no browser is needed and the
arithmetic is pinned exactly.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.models import ResourceType
from app.scanner.network import (
    NetworkCollector,
    is_third_party,
    party_key,
    registrable_domain,
    resource_type_from_cdp,
)

PAGE_URL = "http://localhost:8081/"


# --------------------------------------------------------------------------- #
# Event builders
# --------------------------------------------------------------------------- #


def request_event(
    request_id: str,
    url: str,
    cdp_type: str = "Document",
    initiator: str = "parser",
) -> tuple[str, dict[str, Any]]:
    return (
        "Network.requestWillBeSent",
        {
            "requestId": request_id,
            "request": {"url": url, "method": "GET"},
            "type": cdp_type,
            "initiator": {"type": initiator, "url": PAGE_URL},
        },
    )


def response_event(
    request_id: str,
    url: str,
    cdp_type: str = "Document",
    status: int = 200,
    mime: str = "text/html",
    headers: dict[str, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    return (
        "Network.responseReceived",
        {
            "requestId": request_id,
            "type": cdp_type,
            "response": {
                "url": url,
                "status": status,
                "mimeType": mime,
                "headers": headers or {},
            },
        },
    )


def data_event(request_id: str, decoded: int) -> tuple[str, dict[str, Any]]:
    return ("Network.dataReceived", {"requestId": request_id, "dataLength": decoded})


def finished_event(request_id: str, transfer: int) -> tuple[str, dict[str, Any]]:
    return (
        "Network.loadingFinished",
        {"requestId": request_id, "encodedDataLength": transfer},
    )


def failed_event(request_id: str, error: str = "net::ERR_FAILED"):
    return ("Network.loadingFailed", {"requestId": request_id, "errorText": error})


def feed(collector: NetworkCollector, events: list[tuple[str, dict[str, Any]]]) -> None:
    for method, params in events:
        collector.handle(method, params)


# A complete, realistic page load modelled on the demo site.
DEMO_EVENTS: list[tuple[str, dict[str, Any]]] = [
    *(
        [
            request_event("1", PAGE_URL, "Document"),
            response_event("1", PAGE_URL, "Document", headers={"content-type": "text/html"}),
            data_event("1", 13_259),
            finished_event("1", 13_259),
        ]
    ),
    *(
        [
            request_event("2", "http://localhost:8081/css/main.css", "Stylesheet"),
            response_event(
                "2",
                "http://localhost:8081/css/main.css",
                "Stylesheet",
                mime="text/css",
                headers={"content-type": "text/css"},
            ),
            data_event("2", 10_934),
            finished_event("2", 10_934),
        ]
    ),
    *(
        [
            request_event("3", "http://localhost:8081/js/main.js", "Script"),
            response_event(
                "3",
                "http://localhost:8081/js/main.js",
                "Script",
                mime="application/javascript",
            ),
            data_event("3", 6_073),
            finished_event("3", 6_073),
        ]
    ),
    # Third-party trackers on the other local port.
    *(
        [
            request_event("4", "http://localhost:8082/t/analytics.js", "Script"),
            response_event("4", "http://localhost:8082/t/analytics.js", "Script"),
            data_event("4", 2_707),
            finished_event("4", 2_707),
        ]
    ),
    *(
        [
            request_event("5", "http://localhost:8082/t/adtech.js", "Script"),
            response_event("5", "http://localhost:8082/t/adtech.js", "Script"),
            data_event("5", 2_443),
            finished_event("5", 2_443),
        ]
    ),
    # An image.
    *(
        [
            request_event("6", "http://localhost:8081/assets/a.jpg", "Image"),
            response_event("6", "http://localhost:8081/assets/a.jpg", "Image", mime="image/jpeg"),
            data_event("6", 86_164),
            finished_event("6", 86_164),
        ]
    ),
    # Two fonts.
    *(
        [
            request_event("7", "http://localhost:8081/assets/fonts/a.woff2", "Font"),
            response_event("7", "http://localhost:8081/assets/fonts/a.woff2", "Font"),
            finished_event("7", 36_620),
        ]
    ),
    *(
        [
            request_event("8", "http://localhost:8081/assets/fonts/b.woff2", "Font"),
            response_event("8", "http://localhost:8081/assets/fonts/b.woff2", "Font"),
            finished_event("8", 48_256),
        ]
    ),
    # A video.
    *(
        [
            request_event("9", "http://localhost:8081/assets/hero.mp4", "Media"),
            response_event("9", "http://localhost:8081/assets/hero.mp4", "Media", mime="video/mp4"),
            finished_event("9", 1_000_785),
        ]
    ),
]


@pytest.fixture
def demo_collector() -> NetworkCollector:
    collector = NetworkCollector(PAGE_URL)
    feed(collector, DEMO_EVENTS)
    return collector


# --------------------------------------------------------------------------- #
# Totals
# --------------------------------------------------------------------------- #


def test_total_bytes_sums_encoded_data_length(demo_collector: NetworkCollector):
    summary = demo_collector.summarize()
    expected = 13_259 + 10_934 + 6_073 + 2_707 + 2_443 + 86_164 + 36_620 + 48_256 + 1_000_785
    assert summary.total_bytes == expected


def test_request_count(demo_collector: NetworkCollector):
    assert demo_collector.summarize().request_count == 9


def test_live_total_bytes_tracks_during_the_load():
    """The byte cap (MASTERSPEC §6.1) needs a running total mid-load."""
    collector = NetworkCollector(PAGE_URL)
    assert collector.total_bytes == 0
    feed(collector, [request_event("1", PAGE_URL), finished_event("1", 500)])
    assert collector.total_bytes == 500
    feed(collector, [request_event("2", PAGE_URL + "a.js", "Script"), finished_event("2", 700)])
    assert collector.total_bytes == 1_200


# --------------------------------------------------------------------------- #
# Type mapping
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("cdp", "expected"),
    [
        ("Document", ResourceType.HTML),
        ("Stylesheet", ResourceType.CSS),
        ("Script", ResourceType.JS),
        ("Image", ResourceType.IMG),
        ("Font", ResourceType.FONT),
        ("Media", ResourceType.MEDIA),
        ("XHR", ResourceType.OTHER),
        ("Fetch", ResourceType.OTHER),
        ("WebSocket", ResourceType.OTHER),
        ("Other", ResourceType.OTHER),
        (None, ResourceType.OTHER),
        ("", ResourceType.OTHER),
    ],
)
def test_resource_type_mapping(cdp: str | None, expected: ResourceType):
    assert resource_type_from_cdp(cdp) is expected


def test_by_type_buckets(demo_collector: NetworkCollector):
    by_type = demo_collector.summarize().by_type
    assert by_type[ResourceType.HTML] == 13_259
    assert by_type[ResourceType.CSS] == 10_934
    assert by_type[ResourceType.JS] == 6_073 + 2_707 + 2_443
    assert by_type[ResourceType.IMG] == 86_164
    assert by_type[ResourceType.FONT] == 36_620 + 48_256
    assert by_type[ResourceType.MEDIA] == 1_000_785


def test_by_type_totals_equal_the_grand_total(demo_collector: NetworkCollector):
    summary = demo_collector.summarize()
    assert sum(summary.by_type.values()) == summary.total_bytes


# --------------------------------------------------------------------------- #
# Third-party classification
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.example.com/a", "example.com"),
        ("https://deep.sub.example.co.uk/a", "example.co.uk"),
        ("https://example.com", "example.com"),
        ("http://localhost:8081/", ""),
        ("http://127.0.0.1:8081/", ""),
        ("http://intranet/", ""),
    ],
)
def test_registrable_domain(url: str, expected: str):
    assert registrable_domain(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # With a registrable domain, the port is irrelevant.
        ("https://www.example.com/a", "example.com"),
        ("https://example.com:8443/a", "example.com"),
        ("https://cdn.example.com/a", "example.com"),
        # Without one, host:port is the identity.
        ("http://localhost:8081/", "localhost:8081"),
        ("http://localhost:8082/t/x.js", "localhost:8082"),
        ("http://localhost/", "localhost:80"),
        ("https://localhost/", "localhost:443"),
        ("http://127.0.0.1:9000/", "127.0.0.1:9000"),
    ],
)
def test_party_key(url: str, expected: str):
    assert party_key(url) == expected


@pytest.mark.parametrize(
    ("resource", "page", "expected"),
    [
        # Same registrable domain, different subdomain -> first party.
        ("https://cdn.example.com/a.js", "https://www.example.com/", False),
        ("https://example.com/a.js", "https://example.com/", False),
        # Different registrable domain -> third party.
        ("https://tracker.invalid/a.js", "https://example.com/", True),
        ("https://googletagmanager.com/gtm.js", "https://example.com/", True),
        # The demo's case: same host, different port, no registrable domain.
        ("http://localhost:8082/t/analytics.js", "http://localhost:8081/", True),
        ("http://localhost:8081/js/main.js", "http://localhost:8081/", False),
        # Scheme change alone is not a party change.
        ("https://example.com/a.js", "http://example.com/", False),
        # Unparseable input is not claimed as third party.
        ("data:text/js,1", "https://example.com/", False),
        ("", "https://example.com/", False),
    ],
)
def test_is_third_party(resource: str, page: str, expected: bool):
    assert is_third_party(resource, page) is expected


def test_third_party_summary(demo_collector: NetworkCollector):
    third_party = demo_collector.summarize().third_party
    assert third_party.requests == 2
    assert third_party.bytes == 2_707 + 2_443
    assert third_party.script_bytes == 2_707 + 2_443
    assert third_party.hosts == ["localhost:8082"]


# --------------------------------------------------------------------------- #
# Fonts
# --------------------------------------------------------------------------- #


def test_font_summary(demo_collector: NetworkCollector):
    fonts = demo_collector.summarize().fonts
    assert fonts.count == 2
    assert fonts.bytes == 36_620 + 48_256
    assert len(fonts.urls) == 2


# --------------------------------------------------------------------------- #
# Compression detection
# --------------------------------------------------------------------------- #


def test_uncompressed_text_is_detected(demo_collector: NetworkCollector):
    """Defect UNCOMPRESSED-01: html, css and js with no Content-Encoding."""
    uncompressed = demo_collector.summarize().uncompressed_text
    types = {item.resource_type for item in uncompressed}
    assert types == {ResourceType.HTML, ResourceType.CSS, ResourceType.JS}
    # The two trackers are also uncompressed JS.
    assert len(uncompressed) == 5


def test_compressed_text_is_not_flagged():
    collector = NetworkCollector(PAGE_URL)
    feed(
        collector,
        [
            request_event("1", "http://localhost:8081/a.css", "Stylesheet"),
            response_event(
                "1",
                "http://localhost:8081/a.css",
                "Stylesheet",
                headers={"content-encoding": "gzip"},
            ),
            data_event("1", 40_000),
            finished_event("1", 9_000),
        ],
    )
    assert collector.summarize().uncompressed_text == []


@pytest.mark.parametrize("encoding", ["gzip", "br", "deflate", "zstd", "GZIP", "gzip, br"])
def test_all_compression_encodings_are_recognised(encoding: str):
    collector = NetworkCollector(PAGE_URL)
    feed(
        collector,
        [
            request_event("1", "http://localhost:8081/a.js", "Script"),
            response_event(
                "1", "http://localhost:8081/a.js", "Script", headers={"Content-Encoding": encoding}
            ),
            data_event("1", 40_000),
            finished_event("1", 9_000),
        ],
    )
    assert collector.summarize().uncompressed_text == []


def test_small_text_resources_are_not_flagged():
    """MASTERSPEC §7.3 sets a 2 KB floor."""
    collector = NetworkCollector(PAGE_URL)
    feed(
        collector,
        [
            request_event("1", "http://localhost:8081/tiny.css", "Stylesheet"),
            response_event("1", "http://localhost:8081/tiny.css", "Stylesheet"),
            data_event("1", 1_000),
            finished_event("1", 1_000),
        ],
    )
    assert collector.summarize().uncompressed_text == []


def test_uncompressed_saving_is_seventy_percent_of_decoded():
    collector = NetworkCollector(PAGE_URL)
    feed(
        collector,
        [
            request_event("1", "http://localhost:8081/a.css", "Stylesheet"),
            response_event("1", "http://localhost:8081/a.css", "Stylesheet"),
            data_event("1", 10_000),
            finished_event("1", 10_000),
        ],
    )
    item = collector.summarize().uncompressed_text[0]
    assert item.decoded_bytes == 10_000
    assert item.estimated_saving_bytes == 7_000


def test_images_are_never_flagged_as_uncompressed_text():
    collector = NetworkCollector(PAGE_URL)
    feed(
        collector,
        [
            request_event("1", "http://localhost:8081/a.jpg", "Image"),
            response_event("1", "http://localhost:8081/a.jpg", "Image"),
            data_event("1", 500_000),
            finished_event("1", 500_000),
        ],
    )
    assert collector.summarize().uncompressed_text == []


# --------------------------------------------------------------------------- #
# Redirects, failures, edge cases
# --------------------------------------------------------------------------- #


def test_redirect_hops_are_counted_separately():
    """A redirect reuses the requestId; its bytes must not be lost."""
    collector = NetworkCollector(PAGE_URL)
    collector.handle(*request_event("1", "http://localhost:8081/old"))
    # The redirect arrives as a new requestWillBeSent carrying redirectResponse.
    collector.handle(
        "Network.requestWillBeSent",
        {
            "requestId": "1",
            "request": {"url": "http://localhost:8081/new", "method": "GET"},
            "type": "Document",
            "initiator": {"type": "parser"},
            "redirectResponse": {"status": 302, "encodedDataLength": 300},
        },
    )
    collector.handle(*response_event("1", "http://localhost:8081/new"))
    collector.handle(*finished_event("1", 5_000))

    summary = collector.summarize()
    assert summary.request_count == 2
    assert summary.total_bytes == 5_300
    assert any(r.redirected_to == "http://localhost:8081/new" for r in summary.records)


def test_failed_requests_are_excluded_from_totals():
    collector = NetworkCollector(PAGE_URL)
    feed(
        collector,
        [
            request_event("1", PAGE_URL),
            response_event("1", PAGE_URL),
            finished_event("1", 1_000),
            request_event("2", "http://localhost:8081/missing.js", "Script"),
            failed_event("2"),
        ],
    )
    summary = collector.summarize()
    assert summary.total_bytes == 1_000
    assert summary.request_count == 1
    assert summary.failed_count == 1


def test_unknown_cdp_events_are_ignored():
    collector = NetworkCollector(PAGE_URL)
    collector.handle("Network.webSocketCreated", {"requestId": "x"})
    collector.handle("Page.loadEventFired", {})
    assert collector.summarize().request_count == 0


def test_malformed_events_do_not_raise():
    collector = NetworkCollector(PAGE_URL)
    collector.handle("Network.requestWillBeSent", {})
    collector.handle("Network.responseReceived", {"requestId": "1", "response": "not-a-dict"})
    collector.handle("Network.dataReceived", {"requestId": "1", "dataLength": "nope"})
    collector.handle("Network.loadingFinished", {"requestId": "1"})
    # Should not have blown up.
    collector.summarize()


def test_response_type_overrides_the_request_time_guess():
    """responseReceived carries a more accurate resourceType."""
    collector = NetworkCollector(PAGE_URL)
    collector.handle(*request_event("1", "http://localhost:8081/x", "Other"))
    collector.handle(*response_event("1", "http://localhost:8081/x", "Script"))
    collector.handle(*finished_event("1", 100))
    assert collector.summarize().by_type == {ResourceType.JS: 100}


def test_decoded_bytes_fall_back_to_transfer_bytes():
    """Some responses produce no dataReceived events."""
    collector = NetworkCollector(PAGE_URL)
    feed(
        collector,
        [
            request_event("1", "http://localhost:8081/a.css", "Stylesheet"),
            response_event("1", "http://localhost:8081/a.css", "Stylesheet"),
            finished_event("1", 9_000),
        ],
    )
    item = collector.summarize().uncompressed_text[0]
    assert item.decoded_bytes == 9_000


def test_initiator_is_recorded():
    collector = NetworkCollector(PAGE_URL)
    collector.handle(*request_event("1", "http://localhost:8082/t/a.js", "Script", "script"))
    collector.handle(*finished_event("1", 10))
    record = collector.summarize().records[0]
    assert record.initiator_type == "script"
    assert record.initiator_url == PAGE_URL


def test_live_bytes_count_unfinished_and_failed_responses():
    """The page-weight cap must see bytes before a response finishes."""
    collector = NetworkCollector(PAGE_URL)
    collector.handle(
        "Network.requestWillBeSent",
        {"requestId": "big", "request": {"url": PAGE_URL + "big.js"}, "type": "Script"},
    )
    collector.handle(
        "Network.dataReceived", {"requestId": "big", "dataLength": 700_000, "encodedDataLength": 0}
    )
    assert collector.total_bytes == 0
    assert collector.live_bytes == 700_000

    collector.handle("Network.loadingFailed", {"requestId": "big", "errorText": "net::ERR_ABORTED"})
    assert collector.total_bytes == 0, "failed requests stay out of the carbon total"
    assert collector.live_bytes == 700_000, "but their bytes still count toward the cap"

    collector.handle(
        "Network.requestWillBeSent",
        {"requestId": "ok", "request": {"url": PAGE_URL + "a.css"}, "type": "Stylesheet"},
    )
    collector.handle(
        "Network.dataReceived", {"requestId": "ok", "dataLength": 9_000, "encodedDataLength": 0}
    )
    collector.handle("Network.loadingFinished", {"requestId": "ok", "encodedDataLength": 3_000})
    # A finished response counts its real transfer size, not its decoded size.
    assert collector.live_bytes == 703_000
