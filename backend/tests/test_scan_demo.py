"""Integration: scan the Daily Herald and check every planted defect (MASTERSPEC §15).

``demo-site/DEFECTS.md`` is the source of truth. Each test below names the
DEFECTS.md IDs it covers. Byte figures are asserted as ranges, never exact
values, and the page total is compared against the on-disk size of the files
the page loads (transfer size adds response headers, so it sits a little above).

The demo must be served on localhost:8081 with its tracker host on
localhost:8082, because the page hard-codes the tracker origin. If those ports
already serve the demo (``make demo``) they are reused; otherwise both hosts are
started in-process for the duration of the module.
"""

from __future__ import annotations

import importlib.util
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import HTTPServer
from pathlib import Path

import pytest

from app.config import Settings
from app.models import ImageIssueKind, ResourceType, ScanResult, StepStatus
from app.scanner.pipeline import ScanPipeline

pytestmark = [pytest.mark.integration, pytest.mark.browser]

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_ROOT = REPO_ROOT / "demo-site"
DEMO_URL = "http://localhost:8081/"
GENERATED = DEMO_ROOT / "assets" / "generated"

#: Every file the demo's first load fetches, per DEFECTS.md "First-load byte budget".
DEMO_FILES = [
    DEMO_ROOT / "index.html",
    DEMO_ROOT / "css" / "main.css",
    DEMO_ROOT / "js" / "main.js",
    *(
        DEMO_ROOT / "third-party" / name
        for name in ("analytics.js", "adtech.js", "heatmap.js", "social.js")
    ),
    *(GENERATED / f"article-0{i}.jpg" for i in range(1, 9)),
    GENERATED / "banner-sale.jpg",
    GENERATED / "banner-subscribe.png",
    GENERATED / "hero.mp4",
    *(DEMO_ROOT / "assets" / "fonts").glob("*.woff2"),
]


def _serves_demo(url: str, marker: bytes) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return marker in response.read()
    except (urllib.error.URLError, OSError):
        return False


def _load_demo_server():
    spec = importlib.util.spec_from_file_location("demo_server_for_scan", DEMO_ROOT / "server.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def demo_site() -> Iterator[str]:
    missing = [path.name for path in DEMO_FILES if not path.is_file()]
    if missing:
        pytest.fail(f"demo assets missing ({', '.join(missing)}); run `make assets`")

    if _serves_demo(DEMO_URL, b"The Daily Herald") and _serves_demo(
        "http://localhost:8082/t/analytics.js", b"HeraldAnalytics"
    ):
        yield DEMO_URL
        return

    server_module = _load_demo_server()
    servers: list[HTTPServer] = []
    try:
        for port, root, prefix in ((8081, DEMO_ROOT, ""), (8082, DEMO_ROOT / "third-party", "t")):
            server = server_module.build_server(
                port=port, root=root, compress=False, strip_prefix=prefix, quiet=True
            )
            server_module.serve_forever_in_thread(server)
            servers.append(server)
    except OSError as exc:
        for server in servers:
            server.shutdown()
            server.server_close()
        pytest.skip(f"ports 8081/8082 are busy with something other than the demo: {exc}")

    try:
        yield DEMO_URL
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


@pytest.fixture(scope="module")
def scan(demo_site: str, chromium_executable: str | None, tmp_path_factory) -> ScanResult:
    import asyncio

    settings = Settings(
        allowed_local_hosts=("localhost:8081", "localhost:8082"),
        chromium_executable=chromium_executable,
    )

    async def never(host: str):
        raise AssertionError("green lookup must be skipped for the local demo host")

    pipeline = ScanPipeline(
        demo_site,
        settings=settings,
        screenshot_dir=tmp_path_factory.mktemp("shots"),
        green_checker=never,
    )

    async def run() -> list:
        return [event async for event in pipeline.events()]

    events = asyncio.run(run())
    failed = [e for e in events if e.status is StepStatus.ERROR]
    assert not failed, failed
    assert pipeline.result is not None
    return pipeline.result


def _rule(result: ScanResult, rule_id: str):
    for violation in result.a11y.violations:
        if violation.rule_id == rule_id:
            return violation
    raise AssertionError(
        f"axe rule {rule_id!r} not reported; got {[v.rule_id for v in result.a11y.violations]}"
    )


def _detection(result: ScanResult, name: str):
    for detection in result.carbon.detections:
        if detection.detector == name:
            return detection
    raise AssertionError(
        f"detector {name!r} did not fire; got {[d.detector for d in result.carbon.detections]}"
    )


def _selectors(violation) -> str:
    return " | ".join(node.selector for node in violation.nodes)


def _images_with(result: ScanResult, kind: ImageIssueKind) -> list[str]:
    return sorted(
        image.url.rsplit("/", 1)[-1] for image in result.carbon.images if kind in image.issues
    )


ARTICLES = [f"article-0{i}.jpg" for i in range(1, 9)]


# --------------------------------------------------------------------------- #
# Accessibility defects
# --------------------------------------------------------------------------- #


def test_html_lang_01(scan: ScanResult):
    assert _rule(scan, "html-has-lang").nodes[0].selector == "html"


def test_landmark_01(scan: ScanResult):
    # axe 4.13 reports the missing landmarks through `region` on this page;
    # `landmark-one-main` passes (see DEFECTS.md).
    assert len(_rule(scan, "region").nodes) >= 5


def test_heading_order_01(scan: ScanResult):
    assert "h4" in _selectors(_rule(scan, "heading-order"))


def test_img_alt_01(scan: ScanResult):
    violation = _rule(scan, "image-alt")
    assert len(violation.nodes) == 7
    assert "banner" in _selectors(violation)


def test_link_name_01(scan: ScanResult):
    assert "nav__icon" in _selectors(_rule(scan, "link-name"))


def test_chat_close_01(scan: ScanResult):
    assert "chat__close" in _selectors(_rule(scan, "button-name"))


def test_form_label_01(scan: ScanResult):
    label = _rule(scan, "label")
    select = _rule(scan, "select-name")
    assert "terms-box" in _selectors(label)
    assert "select" in _selectors(select)
    # axe accepts the placeholders on name/email as fallback names, so 2 of the
    # 4 unlabelled controls are flagged (see DEFECTS.md).
    assert len(label.nodes) + len(select.nodes) >= 2


def test_contrast_01(scan: ScanResult):
    selectors = _selectors(_rule(scan, "color-contrast"))
    for expected in ("fine-print", "masthead__date", "card__meta"):
        assert expected in selectors


def test_div_button_01(scan: ScanResult):
    detection = _detection(scan, "div_soup_widgets")
    evidence = " | ".join(detection.evidence)
    assert evidence.count("pager__btn") >= 3
    assert evidence.count("div.tag:nth-of-type") == 4
    assert detection.saves_bytes is False


def test_focus_01(scan: ScanResult):
    # Five section links plus the icon link in `.nav` lose their outline.
    assert scan.keyboard.focus_visible_missing_count >= 5


def test_trap_01(scan: ScanResult):
    assert scan.keyboard.trap_detected is True
    assert scan.keyboard.trap_container == "div#promo"
    assert scan.keyboard.tabs_pressed == 60


# --------------------------------------------------------------------------- #
# Carbon defects
# --------------------------------------------------------------------------- #


def test_video_autoplay_01(scan: ScanResult):
    detection = _detection(scan, "autoplay_media")
    assert 950_000 <= detection.estimated_saving_bytes <= 1_060_000
    (video,) = scan.carbon.autoplay_media
    assert video.kind == "video" and video.autoplay and video.loop and video.muted
    assert not video.has_poster
    assert video.url.endswith("/hero.mp4")


def test_img_oversize_01(scan: ScanResult):
    oversized = _images_with(scan, ImageIssueKind.OVERSIZED)
    assert set(ARTICLES) <= set(oversized)
    # banner-sale renders at 820px from 1600px natural: within the 2x allowance.
    assert "banner-sale.jpg" not in oversized
    for image in scan.carbon.images:
        if image.url.endswith(tuple(ARTICLES)):
            assert (image.natural_w, image.rendered_w) == (3000, 400)
    articles_saving = sum(
        int(image.bytes * (1 - (800 / 3000) ** 2))
        for image in scan.carbon.images
        if image.url.endswith(tuple(ARTICLES))
    )
    assert 600_000 <= articles_saving <= 700_000
    assert _detection(scan, "oversized_image").estimated_saving_bytes >= articles_saving


def test_img_eager_01(scan: ScanResult):
    eager = _images_with(scan, ImageIssueKind.EAGER_BELOW_FOLD)
    assert {f"article-0{i}.jpg" for i in range(3, 9)} <= set(eager)
    assert "banner-subscribe.png" in eager
    assert 600_000 <= _detection(scan, "eager_below_fold").estimated_saving_bytes <= 1_100_000


def test_img_dim_01(scan: ScanResult):
    assert len(_detection(scan, "no_dimensions").evidence) == 10


def test_img_text_01_and_02(scan: ScanResult):
    suspects = _images_with(scan, ImageIssueKind.TEXT_IN_IMAGE_SUSPECTED)
    assert suspects == ["banner-sale.jpg", "banner-subscribe.png"]
    assert 330_000 <= _detection(scan, "text_in_image_suspected").estimated_saving_bytes <= 420_000


def test_third_party_01(scan: ScanResult):
    detection = _detection(scan, "third_party_scripts")
    assert len(detection.evidence) == 4
    assert all(url.startswith("http://localhost:8082/t/") for url in detection.evidence)
    assert 9_000 <= detection.estimated_saving_bytes <= 12_000
    assert scan.carbon.third_party.hosts == ["localhost:8082"]
    assert scan.carbon.third_party.requests == 4


def test_font_bloat_01(scan: ScanResult):
    fonts = scan.carbon.fonts
    assert fonts.count == 4
    assert 200_000 <= fonts.bytes <= 210_000
    assert 138_000 <= _detection(scan, "font_bloat").estimated_saving_bytes <= 150_000


def test_uncompressed_01(scan: ScanResult):
    urls = _detection(scan, "uncompressed_text").evidence
    for expected in ("http://localhost:8081/", "/css/main.css", "/js/main.js"):
        assert any(url.endswith(expected) or url == expected for url in urls), expected
    # social.js is 1,718 bytes, under the 2 KB threshold; the other three count.
    assert sum("localhost:8082/t/" in url for url in urls) == 3
    assert 20_000 <= _detection(scan, "uncompressed_text").estimated_saving_bytes <= 35_000
    assert all(resource.content_encoding == "" for resource in scan.carbon.uncompressed_text)


def test_motion_01(scan: ScanResult):
    detection = _detection(scan, "no_reduced_motion")
    assert detection.saves_bytes is False
    assert any(url.endswith("/css/main.css") for url in detection.evidence)


# --------------------------------------------------------------------------- #
# Page weight
# --------------------------------------------------------------------------- #


def test_total_bytes_within_ten_percent_of_the_files(scan: ScanResult):
    on_disk = sum(path.stat().st_size for path in DEMO_FILES)
    assert len(DEMO_FILES) == 22
    assert abs(scan.carbon.total_bytes - on_disk) <= on_disk * 0.10, (
        scan.carbon.total_bytes,
        on_disk,
    )
    assert scan.carbon.request_count == 22


def test_bytes_by_type_match_the_budget(scan: ScanResult):
    by_type = scan.carbon.by_type
    assert 1_000_000 <= by_type[ResourceType.IMG] <= 1_150_000
    assert 950_000 <= by_type[ResourceType.MEDIA] <= 1_060_000
    assert 195_000 <= by_type[ResourceType.FONT] <= 215_000
    assert sum(by_type.values()) == scan.carbon.total_bytes


def test_carbon_is_labelled_an_estimate(scan: ScanResult):
    carbon = scan.carbon
    assert carbon.is_estimate is True
    # DEFECTS.md: ~2.32 MB is ~0.67 g/view under SWD v3 (grade E band 0.656-0.846).
    assert 0.63 <= carbon.grams_per_view <= 0.72
    assert carbon.assumptions.green_hosted is False
    assert scan.green.source.value == "unavailable"
    assert scan.scores.is_placeholder is True
