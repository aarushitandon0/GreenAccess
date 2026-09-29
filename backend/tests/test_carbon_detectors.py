"""Carbon detector tests (MASTERSPEC §7.3, §15).

The detectors are pure, so almost everything here runs on hand-built facts:
network events fed through the real :class:`NetworkCollector`, and
:class:`PageFacts` as the DOM collector would produce them. One browser test at
the end checks that the DOM collector really yields those facts from HTML.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.carbon import swd
from app.carbon.constants import SWD_MODEL_VERSION
from app.carbon.detectors import (
    analyse_image,
    css_has_motion,
    css_has_reduced_motion_rule,
    detect_autoplay_media,
    detect_div_soup_widgets,
    detect_font_bloat,
    detect_lazy_above_fold,
    detect_no_color_scheme,
    detect_no_reduced_motion,
    detect_third_party_scripts,
    detect_uncompressed_text,
    detect_video_no_captions,
    image_format,
    is_animated_gif,
    run_detectors,
)
from app.carbon.report import build_carbon_result
from app.models import ImageIssueKind, ResourceType
from app.scanner.dom import ImageFact, PageFacts, StylesheetFact, VideoFact
from app.scanner.network import NetworkCollector, NetworkSummary

PAGE = "http://site.test/"


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def feed(
    collector: NetworkCollector,
    request_id: str,
    url: str,
    cdp_type: str,
    transfer: int,
    *,
    mime: str = "",
    decoded: int | None = None,
    encoding: str = "",
    failed: bool = False,
) -> None:
    """Replay the CDP events of one request into `collector`."""
    collector.handle(
        "Network.requestWillBeSent",
        {"requestId": request_id, "request": {"url": url, "method": "GET"}, "type": cdp_type},
    )
    headers = {"Content-Type": mime}
    if encoding:
        headers["Content-Encoding"] = encoding
    collector.handle(
        "Network.responseReceived",
        {
            "requestId": request_id,
            "type": cdp_type,
            "response": {"url": url, "status": 200, "mimeType": mime, "headers": headers},
        },
    )
    if failed:
        collector.handle("Network.loadingFailed", {"requestId": request_id, "errorText": "x"})
        return
    collector.handle(
        "Network.dataReceived",
        {"requestId": request_id, "dataLength": decoded or transfer, "encodedDataLength": transfer},
    )
    collector.handle(
        "Network.loadingFinished", {"requestId": request_id, "encodedDataLength": transfer}
    )


def summary_of(*requests: tuple) -> NetworkSummary:
    collector = NetworkCollector(PAGE)
    for index, request in enumerate(requests):
        url, cdp_type, transfer, *rest = request
        kwargs = rest[0] if rest else {}
        feed(collector, f"r{index}", url, cdp_type, transfer, **kwargs)
    return collector.summarize()


def img(**overrides: object) -> ImageFact:
    base: dict[str, object] = {
        "selector": "img.x",
        "src": "http://site.test/a.jpg",
        "alt": "a photo",
        "has_width_attr": True,
        "has_height_attr": True,
        "loading": "",
        "natural_w": 800,
        "natural_h": 600,
        "rendered_w": 400,
        "rendered_h": 300,
        "doc_top": 100,
    }
    base.update(overrides)
    return ImageFact.model_validate(base)


def facts(**overrides: object) -> PageFacts:
    base: dict[str, object] = {"page_url": PAGE, "viewport_w": 1366, "viewport_h": 768}
    base.update(overrides)
    return PageFacts.model_validate(base)


def by_name(detections: list) -> dict:
    return {d.detector: d for d in detections}


# --------------------------------------------------------------------------- #
# Image format
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("url", "mime", "expected"),
    [
        ("http://x/a.jpg", "image/jpeg", "jpeg"),
        ("http://x/a", "image/png", "png"),
        ("http://x/a.png", "image/webp", "webp"),  # served type beats extension
        ("http://x/a.JPEG", "", "jpeg"),
        ("http://x/a.gif?v=2", "", "gif"),
        ("http://x/a.avif", "", "avif"),
        ("http://x/a.svg", "image/svg+xml; charset=utf-8", "svg"),
        ("data:image/png;base64,AAAA", "", "png"),
        ("http://x/photo", "", ""),
    ],
)
def test_image_format(url: str, mime: str, expected: str):
    assert image_format(url, mime) == expected


# --------------------------------------------------------------------------- #
# oversized_image: natural > 2x rendered; saving = bytes * (1 - (2r/n)^2)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("natural_w", "rendered_w", "bytes_", "expected_saving"),
    [
        # The demo article photos: 3000 natural, 400 rendered -> 0.928888...
        (3000, 400, 100_000, int(100_000 * (1 - (800 / 3000) ** 2))),
        (1000, 100, 50_000, int(50_000 * (1 - (200 / 1000) ** 2))),
        (801, 400, 10_000, int(10_000 * (1 - (800 / 801) ** 2))),
        # Exactly 2x is within the allowance.
        (800, 400, 10_000, None),
        # The demo banners: 1600 natural, 820 rendered -> not oversized.
        (1600, 820, 185_000, None),
        (400, 400, 10_000, None),
        # Not rendered: cannot judge.
        (3000, 0, 10_000, None),
    ],
)
def test_oversized_image(natural_w, rendered_w, bytes_, expected_saving):
    issue, savings = analyse_image(
        img(natural_w=natural_w, rendered_w=rendered_w, rendered_h=100 if rendered_w else 0),
        bytes_=bytes_,
        fmt="webp",
        viewport_h=768,
    )
    if expected_saving is None:
        assert ImageIssueKind.OVERSIZED not in issue.issues
        assert savings.oversized is None
    else:
        assert ImageIssueKind.OVERSIZED in issue.issues
        assert savings.oversized == expected_saving


# --------------------------------------------------------------------------- #
# legacy_format: jpg/png/gif > 30 KB; 30% jpg, 50% png
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("fmt", "bytes_", "flagged", "saving"),
    [
        ("jpeg", 100_000, True, 30_000),
        ("png", 100_000, True, 50_000),
        ("gif", 100_000, True, 0),  # flagged, but §7.3 gives no GIF estimate
        ("jpeg", 30_000, False, None),  # not over the threshold
        ("jpeg", 30_001, True, 9_000),
        ("webp", 500_000, False, None),
        ("avif", 500_000, False, None),
        ("svg", 500_000, False, None),
    ],
)
def test_legacy_format(fmt, bytes_, flagged, saving):
    issue, savings = analyse_image(img(), bytes_=bytes_, fmt=fmt, viewport_h=768)
    assert (ImageIssueKind.LEGACY_FORMAT in issue.issues) is flagged
    assert savings.legacy_format == saving


# --------------------------------------------------------------------------- #
# no_dimensions
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("has_w", "has_h", "flagged"),
    [(True, True, False), (True, False, True), (False, True, True), (False, False, True)],
)
def test_no_dimensions(has_w, has_h, flagged):
    issue, _ = analyse_image(
        img(has_width_attr=has_w, has_height_attr=has_h), bytes_=1_000, fmt="webp", viewport_h=768
    )
    assert (ImageIssueKind.NO_DIMENSIONS in issue.issues) is flagged


# --------------------------------------------------------------------------- #
# eager_below_fold
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("doc_top", "loading", "rendered", "flagged"),
    [
        (800, "", True, True),
        (800, "eager", True, True),
        (768, "", True, True),  # starts exactly at the fold: not in first viewport
        (767, "", True, False),  # a pixel inside the first viewport
        (100, "", True, False),
        (2000, "lazy", True, False),
        (2000, "", False, False),  # display:none etc. has no position to judge
    ],
)
def test_eager_below_fold(doc_top, loading, rendered, flagged):
    fact = img(
        doc_top=doc_top,
        loading=loading,
        rendered_w=400 if rendered else 0,
        rendered_h=300 if rendered else 0,
    )
    issue, savings = analyse_image(fact, bytes_=42_000, fmt="webp", viewport_h=768)
    assert (ImageIssueKind.EAGER_BELOW_FOLD in issue.issues) is flagged
    assert savings.deferred == (42_000 if flagged else None)


# --------------------------------------------------------------------------- #
# text_in_image_suspected
# --------------------------------------------------------------------------- #

LONG_ALT = "HALF-PRICE SALE every subscription tier this week only"


@pytest.mark.parametrize(
    ("natural", "bytes_", "fmt", "alt", "flagged"),
    [
        ((1600, 400), 185_000, "jpeg", LONG_ALT, True),  # IMG-TEXT-01 shape
        ((1600, 400), 194_000, "png", None, True),  # IMG-TEXT-02: no alt at all
        ((1600, 400), 185_000, "jpeg", "", False),  # alt="" = decorative
        ((1600, 400), 185_000, "jpeg", "Harbour at dusk", False),  # short alt
        ((1600, 400), 39_999, "jpeg", LONG_ALT, False),  # too light
        ((1600, 400), 185_000, "webp", LONG_ALT, False),  # not JPEG/PNG
        ((1200, 800), 185_000, "jpeg", LONG_ALT, False),  # not banner-shaped
        ((1200, 400), 185_000, "jpeg", LONG_ALT, True),  # exactly 3:1
        ((1600, 0), 185_000, "jpeg", LONG_ALT, False),  # unknown size
    ],
)
def test_text_in_image_suspected(natural, bytes_, fmt, alt, flagged):
    fact = img(natural_w=natural[0], natural_h=natural[1], rendered_w=820, rendered_h=205, alt=alt)
    issue, savings = analyse_image(fact, bytes_=bytes_, fmt=fmt, viewport_h=768)
    assert (ImageIssueKind.TEXT_IN_IMAGE_SUSPECTED in issue.issues) is flagged
    if flagged:
        assert savings.text_in_image == bytes_ - 2_000


# --------------------------------------------------------------------------- #
# Combined per-image saving
# --------------------------------------------------------------------------- #


def test_combined_saving_compounds_resize_and_reencode():
    fact = img(natural_w=3000, natural_h=2000, rendered_w=400, rendered_h=260, doc_top=2000)
    issue, _ = analyse_image(fact, bytes_=100_000, fmt="jpeg", viewport_h=768)
    resize_saving = int(100_000 * (1 - (800 / 3000) ** 2))
    expected = int(100_000 - (100_000 - resize_saving) * (1 - 0.30))
    assert issue.estimated_saving_bytes == expected
    # Deferral is not removal, so it must not inflate the saving.
    assert issue.estimated_saving_bytes < 100_000


def test_combined_saving_takes_text_replacement_when_larger():
    fact = img(natural_w=1600, natural_h=400, rendered_w=820, rendered_h=205, alt=None)
    issue, _ = analyse_image(fact, bytes_=190_000, fmt="png", viewport_h=768)
    assert issue.estimated_saving_bytes == 188_000


# --------------------------------------------------------------------------- #
# Image roll-up through run_detectors
# --------------------------------------------------------------------------- #


def test_image_detections_count_each_url_once():
    shared = "http://site.test/big.jpg"
    summary = summary_of((shared, "Image", 100_000, {"mime": "image/jpeg"}))
    page = facts(
        images=[
            img(selector="img.a", src=shared, natural_w=3000, rendered_w=400, doc_top=900),
            img(selector="img.b", src=shared, natural_w=3000, rendered_w=400, doc_top=1900),
        ]
    )
    detections = by_name(run_detectors(summary, page, page_url=PAGE).detections)

    oversized = detections["oversized_image"]
    assert oversized.evidence == ["img.a", "img.b"]
    assert oversized.estimated_saving_bytes == int(100_000 * (1 - (800 / 3000) ** 2))
    assert detections["eager_below_fold"].estimated_saving_bytes == 100_000
    assert detections["legacy_format"].estimated_saving_bytes == 30_000


def test_clean_images_produce_no_findings():
    summary = summary_of(("http://site.test/a.webp", "Image", 20_000, {"mime": "image/webp"}))
    page = facts(
        images=[img(src="http://site.test/a.webp", loading="lazy", doc_top=100, rendered_w=400)]
    )
    report = run_detectors(summary, page, page_url=PAGE)
    assert report.images == []
    assert not {"oversized_image", "legacy_format", "no_dimensions"} & set(
        by_name(report.detections)
    )


def test_no_dimensions_is_a_note_not_a_saving():
    summary = summary_of(("http://site.test/a.jpg", "Image", 10_000, {"mime": "image/jpeg"}))
    page = facts(images=[img(has_width_attr=False, has_height_attr=False)])
    detection = by_name(run_detectors(summary, page, page_url=PAGE).detections)["no_dimensions"]
    assert detection.saves_bytes is False
    assert detection.estimated_saving_bytes == 0


# --------------------------------------------------------------------------- #
# autoplay_media
# --------------------------------------------------------------------------- #


def _gif(frames: int) -> bytes:
    images = [Image.new("RGB", (8, 8), (i * 40 % 256, 0, 0)) for i in range(frames)]
    buffer = io.BytesIO()
    images[0].save(buffer, format="GIF", save_all=frames > 1, append_images=images[1:])
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (_gif(1), False),
        (_gif(3), True),
        (b"\x89PNG\r\n\x1a\n", False),
        (b"GIF89a-truncated", False),
        (b"", False),
    ],
)
def test_is_animated_gif(body: bytes, expected: bool):
    assert is_animated_gif(body) is expected


def test_autoplay_video_sums_range_requests():
    video_url = "http://site.test/hero.mp4"
    summary = summary_of(
        (video_url, "Media", 600_000, {"mime": "video/mp4"}),
        (video_url, "Media", 400_000, {"mime": "video/mp4"}),
    )
    page = facts(
        videos=[
            VideoFact(selector="video.hero", src=video_url, autoplay=True, loop=True, muted=True),
            VideoFact(selector="video.clip", src="http://site.test/clip.mp4", autoplay=False),
        ]
    )
    detection, items = detect_autoplay_media(summary, page)
    assert detection is not None
    assert [item.selector for item in items] == ["video.hero"]
    assert items[0].bytes == 1_000_000
    assert items[0].loop and items[0].muted and not items[0].has_poster
    assert detection.estimated_saving_bytes == 1_000_000


def test_heavy_animated_gif_counts_as_autoplay_media():
    gif_url = "http://site.test/spinner.gif"
    summary = summary_of((gif_url, "Image", 250_000, {"mime": "image/gif"}))
    page = facts(images=[img(selector="img.spinner", src=gif_url)])
    detection, items = detect_autoplay_media(summary, page, frozenset({gif_url}))
    assert detection is not None
    assert items[0].kind == "animated_gif"
    assert items[0].selector == "img.spinner"
    assert detection.estimated_saving_bytes == 250_000


def test_light_animated_gif_is_ignored():
    gif_url = "http://site.test/spinner.gif"
    summary = summary_of((gif_url, "Image", 200_000, {"mime": "image/gif"}))
    detection, items = detect_autoplay_media(summary, facts(), frozenset({gif_url}))
    assert detection is None
    assert items == []


def test_no_autoplay_means_no_detection():
    page = facts(videos=[VideoFact(selector="video", src="http://site.test/v.mp4")])
    assert detect_autoplay_media(summary_of(), page) == (None, [])


# --------------------------------------------------------------------------- #
# third_party_scripts
# --------------------------------------------------------------------------- #


def test_third_party_scripts():
    page = "https://www.herald.co.uk/"
    summary = summary_of(
        ("https://www.herald.co.uk/app.js", "Script", 5_000),
        ("https://cdn.herald.co.uk/lib.js", "Script", 7_000),  # same registrable domain
        ("https://tracker.example.com/t.js", "Script", 3_000),
        ("https://ads.example.net/a.js", "Script", 2_000),
        ("https://ads.example.net/pixel.gif", "Image", 50),  # not a script
        ("https://ads.example.net/b.js", "Script", 9_000, {"failed": True}),
    )
    detection = detect_third_party_scripts(summary, page)
    assert detection is not None
    assert detection.estimated_saving_bytes == 5_000
    assert detection.evidence == [
        "https://tracker.example.com/t.js",
        "https://ads.example.net/a.js",
    ]


def test_localhost_ports_are_different_parties():
    """The demo site's trackers live on localhost:8082, the page on :8081."""
    summary = summary_of(("http://localhost:8082/t/a.js", "Script", 2_300))
    detection = detect_third_party_scripts(summary, "http://localhost:8081/")
    assert detection is not None and detection.estimated_saving_bytes == 2_300


def test_first_party_scripts_only():
    summary = summary_of(("http://site.test/app.js", "Script", 5_000))
    assert detect_third_party_scripts(summary, PAGE) is None


# --------------------------------------------------------------------------- #
# font_bloat: > 3 files OR > 150 KB; saving = fonts - 60 KB
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("sizes", "flagged", "saving"),
    [
        ([36_620, 33_584, 48_256, 85_068], True, 203_528 - 60_000),  # the demo
        ([20_000, 20_000, 20_000, 1_000], True, 1_000),  # 4 files, light
        ([160_000], True, 100_000),  # 1 heavy file
        ([30_000, 30_000, 30_000], False, None),
        ([50_000, 50_000, 50_000], False, None),  # exactly 150 KB, 3 files
        ([10_000, 10_000, 10_000, 10_000], True, 0),  # floored at 0
    ],
)
def test_font_bloat(sizes, flagged, saving):
    summary = summary_of(
        *[(f"http://site.test/f{i}.woff2", "Font", size) for i, size in enumerate(sizes)]
    )
    detection = detect_font_bloat(summary)
    assert (detection is not None) is flagged
    if detection is not None:
        assert detection.estimated_saving_bytes == saving
        assert len(detection.evidence) == len(sizes)


# --------------------------------------------------------------------------- #
# uncompressed_text
# --------------------------------------------------------------------------- #


def test_uncompressed_text_rolls_up_seventy_percent():
    summary = summary_of(
        ("http://site.test/", "Document", 13_000, {"mime": "text/html"}),
        ("http://site.test/a.css", "Stylesheet", 10_000, {"mime": "text/css"}),
        ("http://site.test/b.js", "Script", 3_000, {"decoded": 12_000, "encoding": "br"}),
        ("http://site.test/tiny.js", "Script", 1_000),
    )
    detection = detect_uncompressed_text(summary)
    assert detection is not None
    assert detection.evidence == ["http://site.test/", "http://site.test/a.css"]
    assert detection.estimated_saving_bytes == int(13_000 * 0.7) + int(10_000 * 0.7)


def test_all_compressed_means_no_detection():
    summary = summary_of(("http://site.test/a.css", "Stylesheet", 3_000, {"encoding": "gzip"}))
    assert detect_uncompressed_text(summary) is None


# --------------------------------------------------------------------------- #
# no_reduced_motion
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("css", "expected"),
    [
        (".a { animation: 2.6s ease-in-out infinite pulse; }", True),
        (".a { animation-name: spin; }", True),
        (".a { transition: transform 220ms ease, background 220ms ease; }", True),
        (".a { transition: all 0s ease 0s; }", False),
        (".a { transition: none; }", False),
        (".a { animation: none; }", False),
        (".a { transition-property: transform; }", False),  # no duration = 0s
        ("@keyframes spin { from { opacity: 0 } to { opacity: 1 } }", False),
        ("/* .a { animation: spin 1s } */ .b { color: red; }", False),
        (".a { -webkit-animation: x 1s; }", False),  # vendor prefix: not our regex
        ("", False),
    ],
)
def test_css_has_motion(css: str, expected: bool):
    assert css_has_motion(css) is expected


@pytest.mark.parametrize(
    ("css", "expected"),
    [
        ("@media (prefers-reduced-motion: reduce) { * { animation: none; } }", True),
        ("@media screen and (prefers-reduced-motion: no-preference) { .a {} }", True),
        ("/* @media (prefers-reduced-motion: reduce) {} */", False),
        (".a { color: red; }", False),
    ],
)
def test_css_has_reduced_motion_rule(css: str, expected: bool):
    assert css_has_reduced_motion_rule(css) is expected


MOVING = ".tag { transition: transform 220ms ease; }"
REDUCED = "@media (prefers-reduced-motion: reduce) { .tag { transition: none; } }"


@pytest.mark.parametrize(
    ("sheets", "flagged"),
    [
        ([StylesheetFact(href="http://site.test/a.css", css_text=MOVING)], True),
        ([StylesheetFact(css_text=MOVING + REDUCED)], False),
        (
            [
                StylesheetFact(href="http://site.test/a.css", css_text=MOVING),
                StylesheetFact(href="", css_text=REDUCED),
            ],
            False,
        ),
        # A reduced-motion rule in a cross-origin sheet does not count (§7.3).
        (
            [
                StylesheetFact(href="http://site.test/a.css", css_text=MOVING),
                StylesheetFact(href="http://cdn.other/b.css", same_origin=False, css_text=REDUCED),
            ],
            True,
        ),
        ([StylesheetFact(css_text=".a { color: red; }")], False),
        ([], False),
    ],
)
def test_no_reduced_motion(sheets, flagged):
    detection = detect_no_reduced_motion(sheets)
    assert (detection is not None) is flagged
    if detection is not None:
        assert detection.saves_bytes is False
        assert detection.estimated_saving_bytes == 0


# --------------------------------------------------------------------------- #
# Page facts for the trade-off engine
# --------------------------------------------------------------------------- #


def test_div_soup_widgets():
    detection = detect_div_soup_widgets(facts(clickable_non_buttons=["div.a", "div.b"]))
    assert detection is not None
    assert detection.evidence == ["div.a", "div.b"]
    assert detection.saves_bytes is False
    assert detect_div_soup_widgets(facts()) is None


@pytest.mark.parametrize(
    ("css", "meta", "flagged"),
    [
        (".a { color: red; }", "", True),
        ("@media (prefers-color-scheme: dark) { body { background: #121212; } }", "", False),
        (":root { color-scheme: light dark; }", "", False),
        (":root { color-scheme: normal; }", "", True),
        (".a { color: red; }", "light dark", False),
        (".a { color: red; }", "normal", True),
    ],
)
def test_no_color_scheme(css: str, meta: str, flagged: bool):
    page = facts(stylesheets=[StylesheetFact(css_text=css)], meta_color_scheme=meta)
    assert (detect_no_color_scheme(page) is not None) is flagged


def test_video_no_captions():
    videos = [
        VideoFact(selector="video.a", has_captions=False),
        VideoFact(selector="video.b", has_captions=True),
    ]
    detection = detect_video_no_captions(videos)
    assert detection is not None and detection.evidence == ["video.a"]
    assert detect_video_no_captions([]) is None


def test_lazy_above_fold():
    page = facts(
        images=[
            img(selector="img.hero", loading="lazy", doc_top=0),
            img(selector="img.low", loading="lazy", doc_top=2_000),
            img(selector="img.eager", loading="", doc_top=0),
        ]
    )
    detection = detect_lazy_above_fold(page)
    assert detection is not None and detection.evidence == ["img.hero"]


# --------------------------------------------------------------------------- #
# Report assembly
# --------------------------------------------------------------------------- #


def test_build_carbon_result_uses_the_swd_model():
    summary = summary_of(
        ("http://site.test/", "Document", 13_000, {"mime": "text/html"}),
        ("http://site.test/a.jpg", "Image", 500_000, {"mime": "image/jpeg"}),
    )
    report = run_detectors(summary, facts(), page_url=PAGE)
    result = build_carbon_result(summary, report, green=False)

    assert result.total_bytes == 513_000
    assert result.request_count == 2
    assert result.by_type[ResourceType.IMG] == 500_000
    assert result.grams_per_view == pytest.approx(swd.per_visit(513_000))
    assert result.grams_first_visit == pytest.approx(swd.per_byte(513_000))
    assert result.grams_return_visit == pytest.approx(swd.per_byte(513_000 * 0.02))
    assert result.grams_return_visit < result.grams_per_view < result.grams_first_visit
    assert result.is_estimate is True
    assert result.assumptions.model_version == SWD_MODEL_VERSION
    assert result.assumptions.green_hosted is False


def test_green_hosting_lowers_the_estimate():
    summary = summary_of(("http://site.test/", "Document", 1_000_000))
    report = run_detectors(summary, facts(), page_url=PAGE)
    grey = build_carbon_result(summary, report, green=False)
    green = build_carbon_result(summary, report, green=True)
    assert green.grams_per_view < grey.grams_per_view
    assert green.grams_per_view == pytest.approx(swd.per_visit(1_000_000, green=True))
    assert green.assumptions.green_hosted is True


# --------------------------------------------------------------------------- #
# The DOM collector, in a real browser, on an HTML fixture
# --------------------------------------------------------------------------- #

FIXTURE_HTML = """<!doctype html>
<html><head>
<style>
  body { margin: 0; }
  .spacer { height: 1200px; }
  .pulse { animation: pulse 2s infinite; }
  @keyframes pulse { from { opacity: 1 } to { opacity: .5 } }
</style>
</head><body>
  <img id="top" src="/top.png" alt="" width="200" height="100" style="width:200px;height:100px">
  <div class="spacer pulse"></div>
  <img class="low" src="/low.png" loading="eager" style="width:100px">
  <img class="lazy" src="/lazy.png" loading="lazy" style="width:100px">
  <video class="hero" src="/v.mp4" autoplay loop muted></video>
  <div class="fake" onclick="go()">Go</div>
  <span role="button">Also fake</span>
  <button onclick="go()">Real</button>
</body></html>
"""


def _png(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 120, 60)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.browser
async def test_dom_collector_reads_layout_facts(chromium_executable: str | None):
    from playwright.async_api import async_playwright

    from app.scanner.dom import collect_page_facts

    bodies = {
        "/": ("text/html; charset=utf-8", FIXTURE_HTML.encode()),
        "/top.png": ("image/png", _png(1000, 500)),
        "/low.png": ("image/png", _png(300, 300)),
        "/lazy.png": ("image/png", _png(300, 300)),
    }

    async def serve(route):
        path = "/" + route.request.url.split("/", 3)[3]
        content_type, body = bodies.get(path, ("text/plain", b""))
        await route.fulfill(
            status=200 if path in bodies else 404, content_type=content_type, body=body
        )

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=chromium_executable)
        try:
            page = await browser.new_page(viewport={"width": 1366, "height": 768})
            await page.route("**/*", serve)
            await page.goto("http://fixture.test/", wait_until="load")
            result = await collect_page_facts(page)
        finally:
            await browser.close()

    assert (result.viewport_w, result.viewport_h) == (1366, 768)
    images = {image.selector: image for image in result.images}
    top = images["img#top"]
    assert (top.natural_w, top.natural_h, top.rendered_w, top.rendered_h) == (1000, 500, 200, 100)
    assert top.alt == "" and top.has_width_attr and top.has_height_attr
    assert top.doc_top == 0

    low = next(image for image in result.images if "low" in image.selector)
    assert low.alt is None and not low.has_width_attr and low.loading == "eager"
    assert low.doc_top >= 1200
    assert low.src == "http://fixture.test/low.png"

    assert len(result.videos) == 1
    video = result.videos[0]
    assert video.autoplay and video.loop and video.muted and not video.has_poster
    assert not video.has_captions

    assert any("div.fake" in s for s in result.clickable_non_buttons)
    assert any("span" in s for s in result.clickable_non_buttons)
    assert not any("button" in s for s in result.clickable_non_buttons)

    assert result.stylesheets and css_has_motion(result.stylesheets[0].css_text)
    assert result.stylesheets[0].same_origin is True
