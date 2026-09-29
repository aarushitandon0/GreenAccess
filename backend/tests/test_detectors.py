"""Carbon detector tests (MASTERSPEC §7.3, §15).

Every detector is a pure function over DOM facts and a network summary, so
these build both from fixtures and assert the findings and the estimated byte
figures directly.
"""

from __future__ import annotations

import pytest

from app.carbon import detectors
from app.models import ImageIssueKind, ResourceType
from app.scanner.dom import ClickableDivFact, DomFacts, ImageFact, MediaFact
from app.scanner.network import (
    FontSummary,
    NetworkSummary,
    RequestRecord,
    ThirdPartySummary,
    UncompressedResource,
)

PAGE = "http://localhost:8081/"


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def image(
    src: str,
    *,
    natural_w: int = 3000,
    natural_h: int = 2000,
    rendered_w: int = 400,
    rendered_h: int = 260,
    alt: str | None = None,
    has_alt: bool = False,
    width_attr: bool = False,
    height_attr: bool = False,
    loading: str = "eager",
    document_top: float = 2000.0,
    below_fold: bool = True,
    selector: str = "div.card > img",
) -> ImageFact:
    return ImageFact(
        src=src,
        selector=selector,
        natural_w=natural_w,
        natural_h=natural_h,
        rendered_w=rendered_w,
        rendered_h=rendered_h,
        alt=alt,
        has_alt_attribute=has_alt,
        has_width_attribute=width_attr,
        has_height_attribute=height_attr,
        loading=loading,
        document_top=document_top,
        below_fold=below_fold,
    )


def record(url: str, byte_count: int, mime: str = "image/jpeg") -> RequestRecord:
    return RequestRecord(
        request_id=url,
        url=url,
        resource_type=ResourceType.IMG,
        mime_type=mime,
        transfer_bytes=byte_count,
        finished=True,
    )


def summary(records: list[RequestRecord], **kwargs) -> NetworkSummary:
    result = NetworkSummary(records=records)
    result.total_bytes = sum(r.transfer_bytes for r in records)
    result.request_count = len(records)
    for key, value in kwargs.items():
        setattr(result, key, value)
    return result


# --------------------------------------------------------------------------- #
# oversized_image
# --------------------------------------------------------------------------- #


def test_oversized_saving_formula_matches_the_spec():
    """bytes x (1 - (rendered x 2 / natural)^2), per MASTERSPEC §7.3."""
    saving = detectors.oversized_image_saving(100_000, natural_w=3000, rendered_w=400)
    ratio = (400 * 2) / 3000
    assert saving == int(100_000 * (1 - ratio**2))
    # Sanity: a 3000px image shown at 400px wastes ~93% of its bytes.
    assert 0.92 < saving / 100_000 < 0.94


@pytest.mark.parametrize(
    ("natural", "rendered"),
    [(800, 400), (800, 500), (400, 400), (100, 400)],
)
def test_oversized_saving_is_zero_when_within_the_dpr_allowance(natural: int, rendered: int):
    assert detectors.oversized_image_saving(100_000, natural, rendered) == 0


def test_oversized_saving_handles_degenerate_inputs():
    assert detectors.oversized_image_saving(0, 3000, 400) == 0
    assert detectors.oversized_image_saving(100_000, 0, 400) == 0
    assert detectors.oversized_image_saving(100_000, 3000, 0) == 0


def test_detect_oversized_images():
    dom = DomFacts(images=[image("http://localhost:8081/a.jpg")])
    net = summary([record("http://localhost:8081/a.jpg", 86_164)])

    findings = detectors.detect_oversized_images(dom, net)

    assert len(findings) == 1
    assert findings[0].detector == "oversized_image"
    assert 78_000 < findings[0].estimated_saving_bytes < 82_000
    assert "3000x2000" in findings[0].summary


def test_appropriately_sized_image_is_not_flagged():
    dom = DomFacts(images=[image("http://localhost:8081/a.jpg", natural_w=800, rendered_w=400)])
    net = summary([record("http://localhost:8081/a.jpg", 40_000)])
    assert detectors.detect_oversized_images(dom, net) == []


# --------------------------------------------------------------------------- #
# legacy_format
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("mime", "expected_ratio"),
    [("image/jpeg", 0.30), ("image/png", 0.50), ("image/gif", 0.50)],
)
def test_legacy_format_savings_by_type(mime: str, expected_ratio: float):
    url = "http://localhost:8081/a"
    dom = DomFacts(images=[image(url)])
    net = summary([record(url, 100_000, mime=mime)])

    findings = detectors.detect_legacy_formats(dom, net)

    assert len(findings) == 1
    assert findings[0].estimated_saving_bytes == int(100_000 * expected_ratio)


def test_modern_formats_are_not_flagged():
    for mime in ("image/webp", "image/avif", "image/svg+xml"):
        url = "http://localhost:8081/a"
        dom = DomFacts(images=[image(url)])
        net = summary([record(url, 100_000, mime=mime)])
        assert detectors.detect_legacy_formats(dom, net) == [], mime


def test_small_legacy_images_are_not_flagged():
    """MASTERSPEC §7.3 sets a 30 KB floor."""
    url = "http://localhost:8081/a.jpg"
    dom = DomFacts(images=[image(url)])
    net = summary([record(url, 20_000)])
    assert detectors.detect_legacy_formats(dom, net) == []


def test_format_is_inferred_from_the_extension_when_mime_is_missing():
    url = "http://localhost:8081/a.png"
    dom = DomFacts(images=[image(url)])
    net = summary([record(url, 100_000, mime="")])
    findings = detectors.detect_legacy_formats(dom, net)
    assert len(findings) == 1
    assert findings[0].estimated_saving_bytes == 50_000


# --------------------------------------------------------------------------- #
# no_dimensions
# --------------------------------------------------------------------------- #


def test_missing_dimensions_detected_and_saves_no_bytes():
    dom = DomFacts(images=[image("a.jpg"), image("b.jpg")])
    findings = detectors.detect_missing_dimensions(dom, summary([]))

    assert len(findings) == 1
    assert findings[0].estimated_saving_bytes == 0
    assert findings[0].saves_bytes is False
    assert len(findings[0].evidence) == 2


def test_images_with_both_dimensions_are_not_flagged():
    dom = DomFacts(images=[image("a.jpg", width_attr=True, height_attr=True)])
    assert detectors.detect_missing_dimensions(dom, summary([])) == []


def test_width_without_height_is_still_flagged():
    dom = DomFacts(images=[image("a.jpg", width_attr=True, height_attr=False)])
    assert len(detectors.detect_missing_dimensions(dom, summary([]))) == 1


# --------------------------------------------------------------------------- #
# eager_below_fold
# --------------------------------------------------------------------------- #


def test_eager_below_fold_reports_full_bytes():
    url = "http://localhost:8081/a.jpg"
    dom = DomFacts(images=[image(url, below_fold=True, loading="eager")])
    net = summary([record(url, 86_164)])

    findings = detectors.detect_eager_below_fold(dom, net)

    assert len(findings) == 1
    assert findings[0].estimated_saving_bytes == 86_164


def test_lazy_below_fold_image_is_not_flagged():
    url = "http://localhost:8081/a.jpg"
    dom = DomFacts(images=[image(url, below_fold=True, loading="lazy")])
    assert detectors.detect_eager_below_fold(dom, summary([record(url, 86_164)])) == []


def test_above_fold_eager_image_is_not_flagged():
    """Lazy-loading above the fold would hurt, not help (MASTERSPEC §8.1)."""
    url = "http://localhost:8081/a.jpg"
    dom = DomFacts(images=[image(url, below_fold=False, document_top=10)])
    assert detectors.detect_eager_below_fold(dom, summary([record(url, 86_164)])) == []


# --------------------------------------------------------------------------- #
# text_in_image_suspected
# --------------------------------------------------------------------------- #


def test_wide_banner_with_long_alt_is_suspected():
    url = "http://localhost:8081/banner.jpg"
    dom = DomFacts(
        images=[
            image(
                url,
                natural_w=1600,
                natural_h=400,
                rendered_w=820,
                rendered_h=205,
                alt="HALF-PRICE SALE Every subscription tier this week only claim offer now",
                has_alt=True,
            )
        ]
    )
    net = summary([record(url, 185_672)])

    findings = detectors.detect_text_in_images(dom, net)

    assert len(findings) == 1
    assert findings[0].estimated_saving_bytes == 185_672 - 2048
    assert "suspected" in findings[0].summary


def test_wide_banner_with_no_alt_is_suspected():
    url = "http://localhost:8081/banner.png"
    dom = DomFacts(
        images=[image(url, natural_w=1600, natural_h=400, rendered_w=820, has_alt=False, alt=None)]
    )
    net = summary([record(url, 194_004, mime="image/png")])
    assert len(detectors.detect_text_in_images(dom, net)) == 1


def test_square_image_is_not_suspected():
    """The heuristic requires a banner aspect ratio."""
    url = "http://localhost:8081/photo.jpg"
    dom = DomFacts(images=[image(url, natural_w=1000, natural_h=1000, alt="A" * 60, has_alt=True)])
    assert detectors.detect_text_in_images(dom, summary([record(url, 185_672)])) == []


def test_small_banner_is_not_suspected():
    url = "http://localhost:8081/banner.jpg"
    dom = DomFacts(images=[image(url, natural_w=1600, natural_h=400, alt="A" * 60, has_alt=True)])
    assert detectors.detect_text_in_images(dom, summary([record(url, 20_000)])) == []


def test_wide_banner_with_short_alt_is_not_suspected():
    url = "http://localhost:8081/banner.jpg"
    dom = DomFacts(images=[image(url, natural_w=1600, natural_h=400, alt="Sunset", has_alt=True)])
    assert detectors.detect_text_in_images(dom, summary([record(url, 185_672)])) == []


# --------------------------------------------------------------------------- #
# autoplay_media
# --------------------------------------------------------------------------- #


def test_autoplay_video_reports_full_bytes():
    url = "http://localhost:8081/hero.mp4"
    dom = DomFacts(
        media=[
            MediaFact(
                src=url,
                selector="video.hero__video",
                autoplay=True,
                loop=True,
                muted=True,
                has_poster=False,
            )
        ]
    )
    net = summary(
        [
            RequestRecord(
                request_id="v",
                url=url,
                resource_type=ResourceType.MEDIA,
                mime_type="video/mp4",
                transfer_bytes=1_000_785,
                finished=True,
            )
        ]
    )

    findings = detectors.detect_autoplay_media(dom, net)

    assert len(findings) == 1
    assert findings[0].estimated_saving_bytes == 1_000_785


def test_non_autoplay_video_is_not_flagged():
    url = "http://localhost:8081/hero.mp4"
    dom = DomFacts(media=[MediaFact(src=url, selector="video", autoplay=False)])
    net = summary([record(url, 1_000_785, mime="video/mp4")])
    assert detectors.detect_autoplay_media(dom, net) == []


def test_large_animated_gif_counts_as_autoplay_media():
    url = "http://localhost:8081/loop.gif"
    dom = DomFacts(images=[image(url, natural_w=600, natural_h=400, rendered_w=600)])
    net = summary([record(url, 300_000, mime="image/gif")])

    findings = detectors.detect_autoplay_media(dom, net)

    assert len(findings) == 1
    assert findings[0].estimated_saving_bytes == 300_000


def test_small_gif_is_not_autoplay_media():
    url = "http://localhost:8081/icon.gif"
    dom = DomFacts(images=[image(url, natural_w=60, natural_h=40, rendered_w=60)])
    net = summary([record(url, 10_000, mime="image/gif")])
    assert detectors.detect_autoplay_media(dom, net) == []


# --------------------------------------------------------------------------- #
# third_party_scripts, font_bloat, uncompressed_text
# --------------------------------------------------------------------------- #


def test_third_party_scripts_detected():
    net = summary(
        [],
        third_party=ThirdPartySummary(
            requests=4, bytes=9_229, script_bytes=9_229, hosts=["localhost:8082"]
        ),
    )
    findings = detectors.detect_third_party_scripts(DomFacts(), net)
    assert len(findings) == 1
    assert findings[0].estimated_saving_bytes == 9_229
    assert "localhost:8082" in findings[0].summary


def test_no_third_party_scripts_is_no_finding():
    assert detectors.detect_third_party_scripts(DomFacts(), summary([])) == []


def test_font_bloat_on_count():
    net = summary([], fonts=FontSummary(count=4, bytes=100_000, urls=["a", "b", "c", "d"]))
    findings = detectors.detect_font_bloat(DomFacts(), net)
    assert len(findings) == 1
    assert findings[0].estimated_saving_bytes == 100_000 - 61_440


def test_font_bloat_on_bytes():
    net = summary([], fonts=FontSummary(count=2, bytes=200_000, urls=["a", "b"]))
    assert len(detectors.detect_font_bloat(DomFacts(), net)) == 1


def test_reasonable_fonts_are_not_flagged():
    """Two files at 85 KB -- which is what GreenAccess's own UI ships."""
    net = summary([], fonts=FontSummary(count=2, bytes=84_876, urls=["a", "b"]))
    assert detectors.detect_font_bloat(DomFacts(), net) == []


def test_uncompressed_text_aggregates_savings():
    net = summary(
        [],
        uncompressed_text=[
            UncompressedResource(
                url="a.css",
                resource_type=ResourceType.CSS,
                decoded_bytes=10_000,
                estimated_saving_bytes=7_000,
            ),
            UncompressedResource(
                url="b.js",
                resource_type=ResourceType.JS,
                decoded_bytes=6_000,
                estimated_saving_bytes=4_200,
            ),
        ],
    )
    findings = detectors.detect_uncompressed_text(DomFacts(), net)
    assert len(findings) == 1
    assert findings[0].estimated_saving_bytes == 11_200


# --------------------------------------------------------------------------- #
# no_reduced_motion
# --------------------------------------------------------------------------- #


def test_animations_without_reduced_motion_rule_are_flagged():
    dom = DomFacts(has_animations=True, has_reduced_motion_rule=False)
    findings = detectors.detect_no_reduced_motion(dom, summary([]))
    assert len(findings) == 1
    assert findings[0].saves_bytes is False
    assert findings[0].estimated_saving_bytes == 0


def test_animations_with_reduced_motion_rule_are_not_flagged():
    dom = DomFacts(has_animations=True, has_reduced_motion_rule=True)
    assert detectors.detect_no_reduced_motion(dom, summary([])) == []


def test_no_animations_means_no_finding():
    dom = DomFacts(has_animations=False, has_reduced_motion_rule=False)
    assert detectors.detect_no_reduced_motion(dom, summary([])) == []


def test_unreadable_stylesheets_are_disclosed():
    """Honesty: say when the answer might be incomplete."""
    dom = DomFacts(has_animations=True, has_reduced_motion_rule=False, unreadable_stylesheets=2)
    finding = detectors.detect_no_reduced_motion(dom, summary([]))[0]
    assert "cross-origin" in finding.summary


# --------------------------------------------------------------------------- #
# div_soup_widgets
# --------------------------------------------------------------------------- #


def test_clickable_divs_are_flagged():
    dom = DomFacts(
        clickable_divs=[
            ClickableDivFact(selector="div.pager__btn", has_onclick=True, text="Load more"),
            ClickableDivFact(selector="div.tag", has_onclick=True, text="Politics"),
        ]
    )
    findings = detectors.detect_div_soup_widgets(dom, summary([]))
    assert len(findings) == 1
    assert len(findings[0].evidence) == 2
    assert findings[0].saves_bytes is False


def test_properly_wired_div_button_is_not_flagged():
    dom = DomFacts(
        clickable_divs=[
            ClickableDivFact(selector="div.ok", role="button", has_tabindex=True, has_onclick=True)
        ]
    )
    assert detectors.detect_div_soup_widgets(dom, summary([])) == []


# --------------------------------------------------------------------------- #
# Image issue table and the registry
# --------------------------------------------------------------------------- #


def test_build_image_issues_collects_every_kind():
    url = "http://localhost:8081/banner.jpg"
    dom = DomFacts(
        images=[
            image(
                url,
                natural_w=3000,
                natural_h=800,
                rendered_w=400,
                rendered_h=106,
                alt="A very long alternative text that reads like transcribed copy",
                has_alt=True,
                loading="eager",
                below_fold=True,
            )
        ]
    )
    net = summary([record(url, 185_672)])

    issues = detectors.build_image_issues(dom, net)

    assert len(issues) == 1
    kinds = set(issues[0].issues)
    assert ImageIssueKind.OVERSIZED in kinds
    assert ImageIssueKind.LEGACY_FORMAT in kinds
    assert ImageIssueKind.NO_DIMENSIONS in kinds
    assert ImageIssueKind.EAGER_BELOW_FOLD in kinds
    assert ImageIssueKind.TEXT_IN_IMAGE_SUSPECTED in kinds


def test_clean_image_produces_no_issue_row():
    url = "http://localhost:8081/ok.webp"
    dom = DomFacts(
        images=[
            image(
                url,
                natural_w=800,
                natural_h=600,
                rendered_w=400,
                rendered_h=300,
                width_attr=True,
                height_attr=True,
                loading="lazy",
                below_fold=True,
                alt="A photograph",
                has_alt=True,
            )
        ]
    )
    net = summary([record(url, 40_000, mime="image/webp")])
    assert detectors.build_image_issues(dom, net) == []


def test_run_all_covers_every_registered_detector():
    """Every detector in MASTERSPEC §7.3's table must be registered.

    A subset check, not equality: §10's trade-off detectors register here too,
    and adding one of those must not fail this test.
    """
    names = {fn.__name__ for fn in detectors.DETECTORS}
    required = {
        "detect_oversized_images",
        "detect_legacy_formats",
        "detect_missing_dimensions",
        "detect_eager_below_fold",
        "detect_text_in_images",
        "detect_autoplay_media",
        "detect_third_party_scripts",
        "detect_font_bloat",
        "detect_uncompressed_text",
        "detect_no_reduced_motion",
        "detect_div_soup_widgets",
    }
    missing = required - names
    assert not missing, f"MASTERSPEC §7.3 detectors not registered: {sorted(missing)}"


def test_run_all_on_empty_input_claims_no_savings():
    """An empty page has nothing to save.

    Some detectors legitimately fire on an empty document (no_color_scheme has
    no media query to find), so the invariant is that nothing claims bytes,
    not that nothing is reported at all.
    """
    findings = detectors.run_all(DomFacts(), summary([]))
    assert all(f.estimated_saving_bytes == 0 for f in findings)
    assert all(not f.saves_bytes for f in findings)


def test_detectors_never_report_negative_savings():
    url = "http://localhost:8081/a.jpg"
    dom = DomFacts(
        images=[image(url)],
        media=[MediaFact(src="v.mp4", selector="video", autoplay=True)],
        has_animations=True,
    )
    net = summary(
        [record(url, 50_000)],
        fonts=FontSummary(count=4, bytes=10_000, urls=["a"]),
    )
    for finding in detectors.run_all(dom, net):
        assert finding.estimated_saving_bytes >= 0, finding.detector
