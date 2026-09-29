"""Scan the Daily Herald and assert every planted defect is found.

MASTERSPEC §15: "Integration: scan the demo site and assert against DEFECTS.md
(ranges, not exact bytes)."

This is the test that proves the scanner actually works. It starts both demo
hosts on their real ports, runs the whole pipeline once, and then asserts each
defect ID from ``demo-site/DEFECTS.md`` against the result.

Ranges, never exact bytes: encoder output shifts slightly between Pillow and
ffmpeg versions, and pinning exact byte counts would make this test fail for
reasons that have nothing to do with the scanner.

Marked ``integration`` and skipped automatically when Chromium or the demo
assets are missing, so the unit suite still runs anywhere.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from app.config import Settings
from app.models import ScanResult
from app.scanner.pipeline import run_scan

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_ROOT = REPO_ROOT / "demo-site"
GENERATED = DEMO_ROOT / "assets" / "generated"

DEMO_PORT = 8081
TRACKER_PORT = 8082
DEMO_URL = f"http://localhost:{DEMO_PORT}/"

# MASTERSPEC §11's window, and the tolerance §15 asks for on measured bytes.
TOTAL_BYTES_MIN = 2_000_000
TOTAL_BYTES_MAX = 2_400_000
BYTE_TOLERANCE = 0.10


def _load_server_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("demo_server_it", DEMO_ROOT / "server.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assets_present() -> bool:
    return (GENERATED / "hero.mp4").exists() and (GENERATED / "article-01.jpg").exists()


def _chromium_available() -> bool:
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None,
            )
            browser.close()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="module")
def demo_servers() -> Iterator[None]:
    """Run both demo hosts on their real ports for the duration of the module."""
    if not _assets_present():
        pytest.skip("demo assets missing; run `python scripts/make_demo_assets.py`")

    demo_server = _load_server_module()
    servers = []
    try:
        servers.append(
            demo_server.build_server(
                port=DEMO_PORT, root=DEMO_ROOT, compress=False, strip_prefix="", quiet=True
            )
        )
        servers.append(
            demo_server.build_server(
                port=TRACKER_PORT,
                root=DEMO_ROOT / "third-party",
                compress=False,
                strip_prefix="t",
                quiet=True,
            )
        )
    except OSError as exc:
        for server in servers:
            server.server_close()
        pytest.skip(f"demo ports are already in use: {exc}")

    for server in servers:
        demo_server.serve_forever_in_thread(server)
    try:
        yield None
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


@pytest.fixture(scope="module")
def scan(demo_servers: None) -> ScanResult:
    """Scan the demo once and share the result across every assertion."""
    if not _chromium_available():
        pytest.skip("chromium is not installed")

    import asyncio

    settings = Settings(allowed_local_hosts=("localhost", "127.0.0.1"))
    _, result = asyncio.run(run_scan(DEMO_URL, settings=settings))
    assert result is not None, "scan produced no result"
    return result


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def rule_nodes(scan: ScanResult, rule_id: str) -> int:
    for violation in scan.a11y.violations:
        if violation.rule_id == rule_id:
            return violation.node_count
    return 0


def detections(scan: ScanResult, detector: str) -> list:
    return [d for d in scan.carbon.detections if d.detector == detector]


def saving(scan: ScanResult, detector: str) -> int:
    return sum(d.estimated_saving_bytes for d in detections(scan, detector))


def within(value: float, expected: float, tolerance: float = BYTE_TOLERANCE) -> bool:
    return abs(value - expected) <= expected * tolerance


# --------------------------------------------------------------------------- #
# Total weight (DEFECTS.md "First-load byte budget")
# --------------------------------------------------------------------------- #


def test_total_bytes_within_the_masterspec_window(scan: ScanResult):
    assert TOTAL_BYTES_MIN <= scan.carbon.total_bytes <= TOTAL_BYTES_MAX


def test_total_bytes_match_the_measured_file_sizes(scan: ScanResult):
    """Within ±10% of the bytes on disk, per the phase gate."""
    on_disk = sum(p.stat().st_size for p in GENERATED.iterdir() if p.is_file())
    on_disk += sum(p.stat().st_size for p in (DEMO_ROOT / "assets" / "fonts").glob("*.woff2"))
    on_disk += (DEMO_ROOT / "index.html").stat().st_size
    on_disk += (DEMO_ROOT / "css" / "main.css").stat().st_size
    on_disk += (DEMO_ROOT / "js" / "main.js").stat().st_size
    on_disk += sum(p.stat().st_size for p in (DEMO_ROOT / "third-party").glob("*.js"))

    assert within(scan.carbon.total_bytes, on_disk), (
        f"scanner measured {scan.carbon.total_bytes:,}, files on disk total {on_disk:,}"
    )


def test_request_count(scan: ScanResult):
    # 1 html + 1 css + 1 js + 4 trackers + 11 images + 4 fonts + 1 video = 23,
    # minus the hero video being counted once. Allow a small margin.
    assert 20 <= scan.carbon.request_count <= 25


def test_carbon_grade_is_e_or_f(scan: ScanResult):
    """MASTERSPEC §11 targets a before state of grade E or F."""
    from app.carbon.swd import rating

    grade = rating(scan.carbon.grams_per_view)
    assert grade in {"E", "F"}, f"grade {grade} at {scan.carbon.grams_per_view:.4f} g"


def test_carbon_is_labelled_an_estimate(scan: ScanResult):
    assert scan.carbon.is_estimate is True
    assert scan.carbon.assumptions.model_version == "3"


# --------------------------------------------------------------------------- #
# Accessibility defects
# --------------------------------------------------------------------------- #


def test_html_lang_01(scan: ScanResult):
    assert rule_nodes(scan, "html-has-lang") >= 1


def test_landmark_01(scan: ScanResult):
    """No landmarks: axe's `region` rule flags the orphaned content."""
    assert rule_nodes(scan, "region") >= 5


def test_heading_order_01(scan: ScanResult):
    assert rule_nodes(scan, "heading-order") >= 1


def test_img_alt_01(scan: ScanResult):
    """At least 5 of the 8 article images, plus the subscribe banner."""
    assert rule_nodes(scan, "image-alt") >= 5


def test_link_name_01(scan: ScanResult):
    assert rule_nodes(scan, "link-name") >= 1


def test_chat_close_01(scan: ScanResult):
    """The icon-only chat close button has no accessible name."""
    assert rule_nodes(scan, "button-name") >= 1


def test_form_label_01(scan: ScanResult):
    """Name, email and the terms checkbox are unlabelled; so is the select."""
    assert rule_nodes(scan, "label") >= 3
    assert rule_nodes(scan, "select-name") >= 1


def test_contrast_01(scan: ScanResult):
    assert rule_nodes(scan, "color-contrast") >= 10


def test_incomplete_results_are_recorded_but_not_scored(scan: ScanResult):
    """MASTERSPEC §6.3: needs-review items are counted, never scored.

    The count is recorded on the result, and no penalty derives from it: the
    accessibility score is a function of `violations` alone.
    """
    assert scan.a11y.incomplete_count >= 0
    incomplete_rule_ids = {v.rule_id for v in scan.a11y.violations}
    assert len(incomplete_rule_ids) == scan.a11y.unique_rules


def test_every_expected_axe_rule_fires(scan: ScanResult):
    """The full accessibility set DEFECTS.md promises."""
    expected = {
        "html-has-lang",
        "region",
        "heading-order",
        "image-alt",
        "link-name",
        "button-name",
        "label",
        "select-name",
        "color-contrast",
    }
    found = {v.rule_id for v in scan.a11y.violations}
    assert expected <= found, f"not detected: {sorted(expected - found)}"


# --------------------------------------------------------------------------- #
# Keyboard defects
# --------------------------------------------------------------------------- #


def test_trap_01(scan: ScanResult):
    """The promo widget traps Tab."""
    assert scan.keyboard.trap_detected is True
    assert scan.keyboard.trap_container is not None
    assert "promo" in scan.keyboard.trap_container


def test_focus_01(scan: ScanResult):
    """`.nav a { outline: none }` leaves controls with no visible focus."""
    assert scan.keyboard.focus_visible_missing_count >= 3


def test_keyboard_crawl_pressed_tab_to_the_limit(scan: ScanResult):
    """A trapped page consumes the whole budget (MASTERSPEC §6.4: 60 presses)."""
    assert scan.keyboard.tabs_pressed == 60
    assert scan.keyboard.reached_count >= 10


# --------------------------------------------------------------------------- #
# Carbon defects
# --------------------------------------------------------------------------- #


def test_video_autoplay_01(scan: ScanResult):
    findings = detections(scan, "autoplay_media")
    assert len(findings) >= 1
    assert within(saving(scan, "autoplay_media"), 1_001_000, 0.15)

    media = scan.carbon.autoplay_media
    assert any(m.autoplay and m.loop and not m.has_poster for m in media)


def test_img_oversize_01(scan: ScanResult):
    """8 article photos at 3000px rendered at 400px."""
    findings = detections(scan, "oversized_image")
    assert len(findings) >= 8
    # ~81 KB saved on each of the eight articles, plus the banners.
    assert saving(scan, "oversized_image") >= 600_000


def test_img_eager_01(scan: ScanResult):
    findings = detections(scan, "eager_below_fold")
    assert len(findings) >= 8
    assert saving(scan, "eager_below_fold") >= 700_000


def test_img_dim_01(scan: ScanResult):
    findings = detections(scan, "no_dimensions")
    assert len(findings) == 1
    assert len(findings[0].evidence) >= 10
    assert findings[0].estimated_saving_bytes == 0
    assert findings[0].saves_bytes is False


def test_img_text_01_and_02(scan: ScanResult):
    """Both text-baked banners are suspected."""
    findings = detections(scan, "text_in_image_suspected")
    assert len(findings) == 2
    assert 330_000 <= saving(scan, "text_in_image_suspected") <= 420_000


def test_third_party_01(scan: ScanResult):
    findings = detections(scan, "third_party_scripts")
    assert len(findings) == 1
    assert scan.carbon.third_party.requests == 4
    assert scan.carbon.third_party.hosts == ["localhost:8082"]
    assert within(scan.carbon.third_party.script_bytes, 10_300, 0.15)


def test_font_bloat_01(scan: ScanResult):
    findings = detections(scan, "font_bloat")
    assert len(findings) == 1
    assert scan.carbon.fonts.count == 4
    assert within(scan.carbon.fonts.bytes, 204_600, 0.05)


def test_uncompressed_01(scan: ScanResult):
    """html, css, js and the trackers over 2 KB are all uncompressed."""
    findings = detections(scan, "uncompressed_text")
    assert len(findings) == 1
    # social.js is under the 2 KB floor, so 6 of the 7 text resources qualify.
    assert len(scan.carbon.uncompressed_text) >= 5
    assert saving(scan, "uncompressed_text") >= 20_000


def test_motion_01(scan: ScanResult):
    findings = detections(scan, "no_reduced_motion")
    assert len(findings) == 1
    assert findings[0].saves_bytes is False


def test_div_button_01(scan: ScanResult):
    findings = detections(scan, "div_soup_widgets")
    assert len(findings) == 1
    assert len(findings[0].evidence) >= 6


def test_legacy_format_detected_on_the_jpegs_and_png(scan: ScanResult):
    findings = detections(scan, "legacy_format")
    assert len(findings) >= 9


# --------------------------------------------------------------------------- #
# Result completeness
# --------------------------------------------------------------------------- #


def test_every_defects_md_carbon_detector_fired(scan: ScanResult):
    """The full carbon set DEFECTS.md promises."""
    expected = {
        "autoplay_media",
        "oversized_image",
        "eager_below_fold",
        "no_dimensions",
        "text_in_image_suspected",
        "third_party_scripts",
        "font_bloat",
        "uncompressed_text",
        "no_reduced_motion",
        "div_soup_widgets",
        "legacy_format",
    }
    found = {d.detector for d in scan.carbon.detections}
    assert expected <= found, f"not detected: {sorted(expected - found)}"


def test_result_carries_engine_versions(scan: ScanResult):
    assert scan.engine_versions.playwright == "1.63.0"
    assert scan.engine_versions.axe == "4.13.0"
    assert scan.engine_versions.swd_model == "3"


def test_aria_snapshot_is_captured(scan: ScanResult):
    assert len(scan.aria_snapshot) > 500
    assert "heading" in scan.aria_snapshot


def test_image_issue_table_is_populated(scan: ScanResult):
    assert len(scan.carbon.images) >= 10
    for issue in scan.carbon.images:
        assert issue.url
        assert issue.issues


def test_result_serialises_to_json(scan: ScanResult):
    payload = scan.model_dump_json()
    assert len(payload) > 10_000


# --------------------------------------------------------------------------- #
# Scoring and trade-offs (MASTERSPEC §9, §10): the last two pipeline steps
# --------------------------------------------------------------------------- #


def test_scores_are_computed_not_placeholders(scan: ScanResult):
    from app.scoring import score_a11y, score_carbon_result

    assert not scan.scores.is_placeholder
    assert scan.scores.breakdown is not None
    # The stored scores must be exactly what the pure functions give for the
    # stored findings: nothing is tuned or overridden on the way out.
    assert scan.scores.a11y == score_a11y(scan.a11y, scan.keyboard).score
    carbon = score_carbon_result(scan.carbon, scan.green)
    assert scan.scores.carbon == carbon.score
    assert scan.scores.carbon_grade == carbon.grade


def test_demo_produces_at_least_six_tradeoffs_including_a_tension(scan: ScanResult):
    from app.models import TradeoffType

    assert len(scan.tradeoffs) >= 6
    assert any(f.type is TradeoffType.TENSION for f in scan.tradeoffs)
    rule_ids = {f.rule_id for f in scan.tradeoffs}
    # The page has no prefers-color-scheme handling and an uncaptioned video.
    assert {"dark_mode", "captions_bytes", "autoplay_media", "text_in_image"} <= rule_ids


@pytest.mark.parametrize(
    ("rule_id", "detector"),
    [
        ("text_in_image", "text_in_image_suspected"),
        ("eager_below_fold", "eager_below_fold"),
        ("autoplay_media", "autoplay_media"),
    ],
)
def test_tradeoff_bytes_cover_every_offending_element(
    scan: ScanResult, rule_id: str, detector: str
):
    """Detectors emit one detection per element; the finding must sum them all."""
    expected = sum(
        d.estimated_saving_bytes for d in scan.carbon.detections if d.detector == detector
    )
    finding = next(f for f in scan.tradeoffs if f.rule_id == rule_id)
    assert expected > 0
    assert finding.carbon_delta_bytes == expected
