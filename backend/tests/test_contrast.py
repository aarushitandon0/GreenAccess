"""Deterministic contrast fix (MASTERSPEC §8.1 kind 6, §15 "contrast algorithm")."""

from __future__ import annotations

import pytest

from app.patcher.contrast import (
    LARGE_TEXT_RATIO,
    NORMAL_TEXT_RATIO,
    ColorError,
    contrast_ratio,
    is_large_text,
    nearest_accessible_color,
    parse_axe_contrast_summary,
    parse_color,
    relative_luminance,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("#fff", (255, 255, 255)),
        ("#B8B8B8", (184, 184, 184)),
        ("rgb(20, 80, 140)", (20, 80, 140)),
        ("rgba(0,0,0,1)", (0, 0, 0)),
    ],
)
def test_parse_color(raw: str, expected: tuple[int, int, int]) -> None:
    assert parse_color(raw) == expected


@pytest.mark.parametrize("raw", ["red", "#12", "rgba(0,0,0,0.5)", "rgb(300,0,0)", ""])
def test_parse_color_rejects(raw: str) -> None:
    with pytest.raises(ColorError):
        parse_color(raw)


@pytest.mark.parametrize(
    ("a", "b", "ratio"),
    [
        ((0, 0, 0), (255, 255, 255), 21.0),
        ((255, 255, 255), (255, 255, 255), 1.0),
        # WebAIM's checker gives 1.98:1 for #B8B8B8 on white (DEFECTS.md "≈1.9").
        ((184, 184, 184), (255, 255, 255), 1.98),
        ((118, 118, 118), (255, 255, 255), 4.54),
    ],
)
def test_contrast_ratio_matches_known_values(a, b, ratio: float) -> None:
    assert contrast_ratio(a, b) == pytest.approx(ratio, abs=0.01)


def test_luminance_bounds() -> None:
    assert relative_luminance((0, 0, 0)) == 0.0
    assert relative_luminance((255, 255, 255)) == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("fg", "bg", "target"),
    [
        ("#b8b8b8", "#ffffff", NORMAL_TEXT_RATIO),  # CONTRAST-01 .fine-print
        ("#b0b0b0", "#ffffff", NORMAL_TEXT_RATIO),  # .masthead__date
        ("#b4b4b4", "#ffffff", NORMAL_TEXT_RATIO),  # .card__meta
        ("#c9c9c9", "#ffffff", LARGE_TEXT_RATIO),
        ("#14508c", "#0d1622", NORMAL_TEXT_RATIO),  # dark blue on near-black: goes lighter
        ("#ff0000", "#ffffff", NORMAL_TEXT_RATIO),  # saturated colour keeps its hue
        ("#777777", "#777777", NORMAL_TEXT_RATIO),  # worst case: identical mid grey
    ],
)
def test_result_meets_target_after_hex_rounding(fg: str, bg: str, target: float) -> None:
    fix = nearest_accessible_color(fg, bg, target=target)
    assert fix.ratio_after >= target
    assert contrast_ratio(parse_color(fix.color), parse_color(bg)) >= target
    assert fix.changed


def test_is_nearest_not_just_any_passing_colour() -> None:
    """One 8-bit step back toward the original must fail the target."""
    fix = nearest_accessible_color("#b8b8b8", "#ffffff", target=NORMAL_TEXT_RATIO)
    grey = parse_color(fix.color)[0]
    assert contrast_ratio((grey + 1,) * 3, (255, 255, 255)) < NORMAL_TEXT_RATIO
    assert fix.color == "#767676"  # the well-known lightest AA grey on white


def test_keeps_hue_of_a_coloured_foreground() -> None:
    fix = nearest_accessible_color("#ff8080", "#ffffff", target=NORMAL_TEXT_RATIO)
    r, g, b = parse_color(fix.color)
    assert r > g and g == pytest.approx(b, abs=1)


def test_lighter_direction_chosen_on_dark_background() -> None:
    fix = nearest_accessible_color("#333333", "#000000", target=NORMAL_TEXT_RATIO)
    assert relative_luminance(parse_color(fix.color)) > relative_luminance((51, 51, 51))


def test_already_passing_colour_is_unchanged() -> None:
    fix = nearest_accessible_color("#1a1a1a", "#ffffff", target=NORMAL_TEXT_RATIO)
    assert not fix.changed
    assert fix.ratio_after == fix.ratio_before


def test_impossible_target_raises() -> None:
    with pytest.raises(ColorError):
        nearest_accessible_color("#777777", "#777777", target=7.0)


@pytest.mark.parametrize(
    ("px", "weight", "large"),
    [(24, 400, True), (23.9, 400, False), (18.67, 700, True), (18.5, 700, False), (19, 400, False)],
)
def test_large_text(px: float, weight: int, large: bool) -> None:
    assert is_large_text(px, weight) is large


def test_parse_axe_summary() -> None:
    summary = (
        "Fix any of the following:\n  Element has insufficient color contrast of 2.16 "
        "(foreground color: #b0b0b0, background color: #ffffff, font size: 9.8pt (13px), "
        "font weight: normal). Expected contrast ratio of 4.5:1"
    )
    facts = parse_axe_contrast_summary(summary)
    assert facts is not None
    assert (facts.foreground, facts.background) == ("#b0b0b0", "#ffffff")
    assert facts.font_px == 13
    assert facts.font_weight == 400
    assert facts.expected_ratio == 4.5


def test_parse_axe_summary_bold_large_defaults_to_three() -> None:
    summary = (
        "Element has insufficient color contrast of 2.1 (foreground color: #aaaaaa, "
        "background color: #ffffff, font size: 14.0pt (18.6667px), font weight: bold)."
    )
    facts = parse_axe_contrast_summary(summary)
    assert facts is not None and facts.expected_ratio == LARGE_TEXT_RATIO


def test_parse_axe_summary_without_colours_is_none() -> None:
    """Text over an image: axe cannot tell, so neither can we."""
    assert parse_axe_contrast_summary("Element's background color could not be determined") is None
