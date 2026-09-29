"""Trade-off engine tests (MASTERSPEC §10, §15).

The engine must work with no LLM and no network, so every test here builds a
`ScanResult` by hand and asserts on the findings that come back.
"""

from __future__ import annotations

from string import Formatter

import pytest

from app.carbon import swd
from app.models import (
    CarbonResult,
    Detection,
    EngineVersions,
    FontSummary,
    GreenResult,
    GreenSource,
    ImageIssue,
    ImageIssueKind,
    MediaItem,
    ScanResult,
    ThirdPartySummary,
    TradeoffType,
)
from app.tradeoffs.constants import DARK_MODE_CSS_BYTES, WEBVTT_BYTES_PER_VIDEO
from app.tradeoffs.detectors import DETECTOR_FACTS, REGISTRY
from app.tradeoffs.engine import ENGINE_FACTS, evaluate, load_rules

# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def _scan(
    *,
    detections: list[Detection] | None = None,
    images: list[ImageIssue] | None = None,
    media: list[MediaItem] | None = None,
    fonts: FontSummary | None = None,
    third_party: ThirdPartySummary | None = None,
    green: bool = False,
) -> ScanResult:
    return ScanResult(
        carbon=CarbonResult(
            detections=detections or [],
            images=images or [],
            autoplay_media=media or [],
            fonts=fonts or FontSummary(),
            third_party=third_party or ThirdPartySummary(),
        ),
        green=GreenResult(
            host="example.com",
            green=green,
            source=GreenSource.GREENWEB if green else GreenSource.UNAVAILABLE,
        ),
        engine_versions=EngineVersions(playwright="1.63.0", axe="4.10.2", swd_model="3"),
    )


def _detection(name: str, *, saving: int = 0, evidence: list[str] | None = None) -> Detection:
    return Detection(
        detector=name,
        summary=f"{name} found",
        estimated_saving_bytes=saving,
        evidence=evidence or ["#target"],
        saves_bytes=saving != 0,
    )


def _by_id(findings: list, rule_id: str):
    return next((f for f in findings if f.rule_id == rule_id), None)


# --------------------------------------------------------------------------- #
# The rules file itself
# --------------------------------------------------------------------------- #


def test_rules_cover_every_synergy_and_tension_in_masterspec_10() -> None:
    rules = load_rules()
    synergies = {r.id for r in rules if r.type is TradeoffType.SYNERGY}
    tensions = {r.id for r in rules if r.type is TradeoffType.TENSION}

    assert synergies == {
        "text_in_image",
        "autoplay_media",
        "no_reduced_motion",
        "eager_below_fold",
        "third_party_widgets",
        "div_soup_widgets",
    }
    assert tensions == {
        "dark_mode",
        "captions_bytes",
        "lazy_above_fold",
        "high_res_zoom",
        "font_subsetting",
    }


def test_rule_ids_are_unique_and_detectors_are_registered() -> None:
    rules = load_rules()
    assert len({r.id for r in rules}) == len(rules)
    for rule in rules:
        assert rule.detector in REGISTRY


def test_every_rule_has_the_fields_a_card_needs() -> None:
    """MASTERSPEC §10: each card shows a11y impact, carbon impact, type, fix."""
    for rule in load_rules():
        assert rule.title.strip()
        assert rule.a11y_effect.strip()
        assert rule.carbon_effect.note.strip()
        assert rule.explanation_template.strip()
        assert rule.fix_id, f"{rule.id} has no recommended fix"


def test_every_template_placeholder_is_supplied() -> None:
    """A template must never reach the UI with an unfilled hole."""
    for rule in load_rules():
        placeholders = {
            name for _, name, _, _ in Formatter().parse(rule.explanation_template) if name
        }
        available = DETECTOR_FACTS[rule.detector] | ENGINE_FACTS
        missing = placeholders - available
        assert not missing, f"rule {rule.id} needs facts no detector provides: {missing}"


def test_every_registered_detector_declares_its_facts() -> None:
    assert set(DETECTOR_FACTS) == set(REGISTRY)


# --------------------------------------------------------------------------- #
# Nothing fires on a clean page
# --------------------------------------------------------------------------- #


def test_clean_scan_produces_no_findings() -> None:
    assert evaluate(_scan()) == []


def test_every_detector_returns_none_on_a_clean_scan() -> None:
    clean = _scan()
    for name, detector in REGISTRY.items():
        assert detector(clean) is None, f"{name} fired on an empty scan"


# --------------------------------------------------------------------------- #
# Each rule fires on the right signal
# --------------------------------------------------------------------------- #


def test_text_in_image_fires_on_suspected_images() -> None:
    scan = _scan(
        images=[
            ImageIssue(
                url="/assets/banner-sale.jpg",
                bytes=186_000,
                selector=".banner",
                issues=[ImageIssueKind.TEXT_IN_IMAGE_SUSPECTED],
                estimated_saving_bytes=184_000,
            )
        ],
        detections=[_detection("text_in_image_suspected", saving=184_000)],
    )
    finding = _by_id(evaluate(scan), "text_in_image")
    assert finding is not None
    assert finding.type is TradeoffType.SYNERGY
    assert finding.carbon_delta_bytes == 184_000
    assert finding.recommended_fix_id == "text_in_image"
    assert finding.evidence == [".banner"]


def test_autoplay_media_fires_and_counts_media_bytes() -> None:
    scan = _scan(
        media=[
            MediaItem(
                url="/assets/hero.mp4", bytes=1_001_000, selector=".hero__video", autoplay=True
            )
        ],
        detections=[_detection("autoplay_media", saving=1_001_000)],
    )
    finding = _by_id(evaluate(scan), "autoplay_media")
    assert finding is not None
    assert finding.carbon_delta_bytes == 1_001_000
    assert finding.recommended_fix_id == "autoplay_video"


def test_no_reduced_motion_saves_no_bytes_but_still_fires() -> None:
    scan = _scan(detections=[_detection("no_reduced_motion", evidence=["@keyframes drift"])])
    finding = _by_id(evaluate(scan), "no_reduced_motion")
    assert finding is not None
    assert finding.carbon_delta_bytes == 0
    assert finding.carbon_delta_grams == 0.0
    assert "processor" in finding.explanation or "GPU" in finding.explanation


def test_eager_below_fold_fires_and_says_bytes_are_deferred() -> None:
    scan = _scan(
        images=[
            ImageIssue(
                url=f"/assets/article-0{i}.jpg",
                bytes=90_000,
                selector=f".card__image:nth-of-type({i})",
                issues=[ImageIssueKind.EAGER_BELOW_FOLD],
            )
            for i in range(1, 7)
        ],
        detections=[_detection("eager_below_fold", saving=1_077_000)],
    )
    finding = _by_id(evaluate(scan), "eager_below_fold")
    assert finding is not None
    assert finding.carbon_delta_bytes == 1_077_000
    assert "postponed" in finding.explanation or "deferred" in finding.explanation
    assert len(finding.evidence) == 5, "evidence is capped for the card"


@pytest.mark.parametrize(
    ("rule_id", "detector", "savings"),
    [
        # The carbon detectors emit one detection per offending element, which
        # is what the demo site produces: two banners, seven eager images.
        ("text_in_image", "text_in_image_suspected", [192_200, 183_900]),
        ("eager_below_fold", "eager_below_fold", [194_300, 90_900, 90_700, 89_600]),
        ("autoplay_media", "autoplay_media", [1_001_000, 250_000]),
    ],
)
def test_per_element_detections_are_summed_not_just_the_first(
    rule_id: str, detector: str, savings: list[int]
) -> None:
    scan = _scan(
        detections=[
            _detection(detector, saving=saving, evidence=[f"#el-{i}"])
            for i, saving in enumerate(savings)
        ]
    )
    finding = _by_id(evaluate(scan), rule_id)
    assert finding is not None
    assert finding.carbon_delta_bytes == sum(savings)
    assert finding.carbon_delta_grams == pytest.approx(swd.per_visit(sum(savings), green=False))
    assert finding.evidence == [f"#el-{i}" for i in range(len(savings))]
    count = len(savings)
    assert f" {count} " in finding.explanation, "the count must cover every detection"


def test_third_party_widgets_pluralises_hosts_and_requests_separately() -> None:
    scan = _scan(
        third_party=ThirdPartySummary(
            requests=4, bytes=9_200, script_bytes=9_200, hosts=["localhost:8082"]
        ),
        detections=[_detection("third_party_scripts", saving=9_200)],
    )
    finding = _by_id(evaluate(scan), "third_party_widgets")
    assert finding is not None
    assert finding.carbon_delta_bytes == 9_200
    assert "4 requests" in finding.explanation
    assert "1 other registrable domain " in finding.explanation + " "


def test_div_soup_widgets_needs_a_scanner_detection() -> None:
    assert _by_id(evaluate(_scan()), "div_soup_widgets") is None
    scan = _scan(
        detections=[_detection("div_soup_widgets", saving=1_400, evidence=[".pager__btn", ".tag"])]
    )
    finding = _by_id(evaluate(scan), "div_soup_widgets")
    assert finding is not None
    assert finding.recommended_fix_id == "manual_review"


def test_lazy_above_fold_is_a_tension_with_no_byte_change() -> None:
    scan = _scan(detections=[_detection("lazy_above_fold", evidence=[".hero img"])])
    finding = _by_id(evaluate(scan), "lazy_above_fold")
    assert finding is not None
    assert finding.type is TradeoffType.TENSION
    assert finding.carbon_delta_bytes == 0


def test_high_res_zoom_is_computed_from_the_image_table() -> None:
    scan = _scan(
        images=[
            # 780 natural against 400 rendered: under the 2x floor, at risk.
            ImageIssue(url="/a.jpg", natural_w=780, rendered_w=400, selector=".tight"),
            # 3000 against 400: plenty of headroom, safe to compress.
            ImageIssue(url="/b.jpg", natural_w=3000, rendered_w=400, selector=".roomy"),
            # An icon: too small to be worth a finding.
            ImageIssue(url="/c.svg", natural_w=24, rendered_w=24, selector=".icon"),
        ]
    )
    finding = _by_id(evaluate(scan), "high_res_zoom")
    assert finding is not None
    assert finding.evidence == [".tight"]
    assert finding.carbon_delta_bytes == 0
    assert "200%" in finding.explanation


def test_font_subsetting_needs_both_thresholds() -> None:
    too_few = _scan(fonts=FontSummary(count=2, bytes=300_000))
    too_small = _scan(fonts=FontSummary(count=4, bytes=100_000))
    assert _by_id(evaluate(too_few), "font_subsetting") is None
    assert _by_id(evaluate(too_small), "font_subsetting") is None

    scan = _scan(
        fonts=FontSummary(count=4, bytes=203_528, urls=[f"/fonts/f{i}.woff2" for i in range(4)])
    )
    finding = _by_id(evaluate(scan), "font_subsetting")
    assert finding is not None
    assert "203.5 KB" in finding.explanation
    assert finding.carbon_delta_bytes == 0


# --------------------------------------------------------------------------- #
# Dark mode (the rule with the most nuance to get right)
# --------------------------------------------------------------------------- #


def test_dark_mode_does_not_fire_when_the_page_handles_color_scheme() -> None:
    """Only the absence of prefers-color-scheme handling triggers this rule."""
    assert _by_id(evaluate(_scan()), "dark_mode") is None
    scan_with_other_issues = _scan(detections=[_detection("no_reduced_motion")])
    assert _by_id(evaluate(scan_with_other_issues), "dark_mode") is None


def test_dark_mode_fires_when_there_is_no_color_scheme_handling() -> None:
    scan = _scan(detections=[_detection("no_color_scheme", evidence=["document"])])
    finding = _by_id(evaluate(scan), "dark_mode")
    assert finding is not None
    assert finding.type is TradeoffType.TENSION
    assert finding.carbon_delta_bytes == -DARK_MODE_CSS_BYTES
    assert finding.carbon_delta_grams < 0, "adding a theme costs bytes, it does not save them"


@pytest.mark.parametrize(
    ("phrase", "why"),
    [
        ("OLED", "the energy saving is an OLED-screen argument, and must say so"),
        ("halation", "astigmatism halation is the counter-argument"),
        ("photophobia", "light-sensitive readers are the group dark mode relieves"),
        ("#121212", "MASTERSPEC §8.1 fix 13 mandates this background"),
        ("#E8E8E8", "and this off-white text"),
        ("toggle", "a toggle is required, not just the media query"),
        ("prefers-color-scheme", "honour the system preference first"),
    ],
)
def test_dark_mode_explanation_states_the_whole_nuance(phrase: str, why: str) -> None:
    scan = _scan(detections=[_detection("no_color_scheme")])
    finding = _by_id(evaluate(scan), "dark_mode")
    assert finding is not None
    assert phrase in finding.explanation, why


def test_dark_mode_a11y_impact_names_both_sides() -> None:
    scan = _scan(detections=[_detection("no_color_scheme")])
    finding = _by_id(evaluate(scan), "dark_mode")
    assert finding is not None
    assert "photophobia" in finding.a11y_impact
    assert "astigmatism" in finding.a11y_impact


# --------------------------------------------------------------------------- #
# Captions (cost quantified against the video, with the net shown)
# --------------------------------------------------------------------------- #


def test_captions_cost_is_a_small_labelled_constant() -> None:
    scan = _scan(
        media=[
            MediaItem(
                url="/assets/hero.mp4", bytes=1_001_000, selector=".hero__video", autoplay=True
            )
        ],
        detections=[_detection("video_no_captions", evidence=[".hero__video"])],
    )
    finding = _by_id(evaluate(scan), "captions_bytes")
    assert finding is not None
    assert finding.type is TradeoffType.TENSION
    assert finding.carbon_delta_bytes == -WEBVTT_BYTES_PER_VIDEO


def test_captions_explanation_shows_cost_video_and_net() -> None:
    scan = _scan(
        media=[
            MediaItem(
                url="/assets/hero.mp4", bytes=1_001_000, selector=".hero__video", autoplay=True
            )
        ],
        detections=[_detection("video_no_captions", evidence=[".hero__video"])],
    )
    finding = _by_id(evaluate(scan), "captions_bytes")
    assert finding is not None
    explanation = finding.explanation
    assert "2.0 KB" in explanation, "the caption cost"
    assert "1.001 MB" in explanation, "the video it is measured against"
    assert "1.003 MB" in explanation, "the net, which must be visibly different"
    assert "0.20%" in explanation, "the share the captions add"
    assert "estimate" in explanation, "MASTERSPEC §7: byte figures are estimates"


def test_captions_share_is_computed_against_the_matching_video() -> None:
    scan = _scan(
        media=[
            MediaItem(url="/v.mp4", bytes=200_000, selector=".v", autoplay=True),
            MediaItem(url="/other.mp4", bytes=9_000_000, selector=".other", autoplay=True),
        ],
        detections=[_detection("video_no_captions", evidence=[".v"])],
    )
    finding = _by_id(evaluate(scan), "captions_bytes")
    assert finding is not None
    # 2048 / 200000 = 1.02%, not the share against both videos.
    assert "1.02%" in finding.explanation


def test_captions_survive_a_video_of_unknown_size() -> None:
    scan = _scan(detections=[_detection("video_no_captions", evidence=[".v"])])
    finding = _by_id(evaluate(scan), "captions_bytes")
    assert finding is not None
    assert "unknown share" in finding.explanation


# --------------------------------------------------------------------------- #
# Carbon deltas come from the same SWD function as the score
# --------------------------------------------------------------------------- #


def test_grams_use_the_same_swd_function_as_the_carbon_score() -> None:
    scan = _scan(detections=[_detection("text_in_image_suspected", saving=184_000)])
    finding = _by_id(evaluate(scan), "text_in_image")
    assert finding is not None
    assert finding.carbon_delta_grams == pytest.approx(swd.per_visit(184_000, green=False))


def test_grams_honour_the_pages_green_hosting_verdict() -> None:
    detections = [_detection("text_in_image_suspected", saving=184_000)]
    grey = _by_id(evaluate(_scan(detections=detections)), "text_in_image")
    green = _by_id(evaluate(_scan(detections=detections, green=True)), "text_in_image")
    assert grey is not None and green is not None
    assert green.carbon_delta_grams == pytest.approx(swd.per_visit(184_000, green=True))
    assert green.carbon_delta_grams < grey.carbon_delta_grams


def test_a_cost_produces_negative_grams() -> None:
    scan = _scan(detections=[_detection("no_color_scheme")])
    finding = _by_id(evaluate(scan), "dark_mode")
    assert finding is not None
    assert finding.carbon_delta_grams == pytest.approx(-swd.per_visit(DARK_MODE_CSS_BYTES))


# --------------------------------------------------------------------------- #
# Ordering and the full-page case
# --------------------------------------------------------------------------- #


def _daily_herald_shaped_scan() -> ScanResult:
    """A scan result shaped like the demo site's planted defects (§11).

    Byte figures come from `demo-site/DEFECTS.md`. This is a fixture for the
    engine's behaviour, not a claim about a real scan.
    """
    return _scan(
        images=[
            ImageIssue(
                url="/assets/banner-sale.jpg",
                bytes=186_000,
                natural_w=1600,
                rendered_w=1200,
                selector=".banner--sale",
                issues=[ImageIssueKind.TEXT_IN_IMAGE_SUSPECTED],
                estimated_saving_bytes=184_000,
            ),
            *[
                ImageIssue(
                    url=f"/assets/article-0{i}.jpg",
                    bytes=90_000,
                    natural_w=3000,
                    rendered_w=400,
                    selector=f".card__image--{i}",
                    issues=[ImageIssueKind.EAGER_BELOW_FOLD, ImageIssueKind.OVERSIZED],
                )
                for i in range(1, 9)
            ],
        ],
        media=[
            MediaItem(
                url="/assets/hero.mp4",
                bytes=1_001_000,
                selector=".hero__video",
                autoplay=True,
                loop=True,
                muted=True,
            )
        ],
        fonts=FontSummary(count=4, bytes=203_528, urls=[f"/fonts/f{i}.woff2" for i in range(4)]),
        third_party=ThirdPartySummary(
            requests=4, bytes=9_200, script_bytes=9_200, hosts=["localhost:8082"]
        ),
        detections=[
            _detection("text_in_image_suspected", saving=184_000),
            _detection("autoplay_media", saving=1_001_000),
            _detection("eager_below_fold", saving=1_077_000),
            _detection("third_party_scripts", saving=9_200),
            _detection("no_reduced_motion", evidence=["@keyframes drift"]),
            _detection("div_soup_widgets", saving=0, evidence=[".pager__btn", ".tag"]),
            _detection("no_color_scheme", evidence=["document"]),
            _detection("video_no_captions", evidence=[".hero__video"]),
        ],
    )


def test_a_bad_page_produces_at_least_six_findings_including_a_tension() -> None:
    findings = evaluate(_daily_herald_shaped_scan())
    assert len(findings) >= 6
    assert any(f.type is TradeoffType.TENSION for f in findings)
    assert any(f.type is TradeoffType.SYNERGY for f in findings)


def test_synergies_are_listed_before_tensions_biggest_first() -> None:
    findings = evaluate(_daily_herald_shaped_scan())
    types = [f.type for f in findings]
    assert types == sorted(types, key=lambda t: t is not TradeoffType.SYNERGY)

    synergy_bytes = [abs(f.carbon_delta_bytes) for f in findings if f.type is TradeoffType.SYNERGY]
    assert synergy_bytes == sorted(synergy_bytes, reverse=True)


def test_evaluation_is_deterministic() -> None:
    scan = _daily_herald_shaped_scan()
    first = [f.model_dump() for f in evaluate(scan)]
    second = [f.model_dump() for f in evaluate(scan)]
    assert first == second


def test_every_finding_is_presentable() -> None:
    for finding in evaluate(_daily_herald_shaped_scan()):
        assert finding.title.strip()
        assert finding.a11y_impact.strip()
        assert finding.explanation.strip()
        assert "{" not in finding.explanation, "an unfilled placeholder escaped"
        assert finding.recommended_fix_id
