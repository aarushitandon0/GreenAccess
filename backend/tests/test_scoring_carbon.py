"""Carbon score tests (MASTERSPEC §9.2, §15).

Covers the band table, the boundaries between bands, interpolation inside a
band, the green-hosting bonus and its cap, and the invariant that the score
never increases as a page gets dirtier.
"""

from __future__ import annotations

import pytest

from app.carbon.constants import SWDM_V3_RATINGS
from app.models import (
    CarbonAssumptions,
    CarbonGrade,
    CarbonResult,
    GreenResult,
    GreenSource,
)
from app.scoring.carbon import (
    band_bounds,
    grade_for_grams,
    score_carbon,
    score_carbon_result,
)
from app.scoring.constants import (
    CARBON_BAND_MAX_GRAMS,
    CARBON_BAND_ORDER,
    CARBON_BAND_SCORES,
    CARBON_F_ZERO_GRAMS,
    GREEN_HOST_BONUS,
)

# --------------------------------------------------------------------------- #
# The constants themselves
# --------------------------------------------------------------------------- #


def test_bands_match_the_pinned_co2js_table() -> None:
    """The two copies of the band edges must never drift apart.

    `scoring/constants.py` holds them for the score; `carbon/constants.py`
    holds them for the grade lookup inside the SWD port. Both were verified
    against @tgwf/co2 0.19.0 SWDMV3_RATINGS.
    """
    for grade, maximum in CARBON_BAND_MAX_GRAMS.items():
        assert SWDM_V3_RATINGS[grade.value] == maximum


def test_bands_match_masterspec_9_2() -> None:
    assert CARBON_BAND_MAX_GRAMS == {
        CarbonGrade.A_PLUS: 0.095,
        CarbonGrade.A: 0.186,
        CarbonGrade.B: 0.341,
        CarbonGrade.C: 0.493,
        CarbonGrade.D: 0.656,
        CarbonGrade.E: 0.846,
    }


def test_score_ranges_match_masterspec_9_2() -> None:
    assert CARBON_BAND_SCORES == {
        CarbonGrade.A_PLUS: (95, 100),
        CarbonGrade.A: (85, 94),
        CarbonGrade.B: (70, 84),
        CarbonGrade.C: (55, 69),
        CarbonGrade.D: (40, 54),
        CarbonGrade.E: (25, 39),
        CarbonGrade.F: (0, 24),
    }


def test_bands_tile_the_line_without_gaps() -> None:
    previous_upper = 0.0
    for grade in CARBON_BAND_ORDER:
        lower, upper = band_bounds(grade)
        assert lower == previous_upper
        assert upper > lower
        previous_upper = upper
    assert previous_upper == CARBON_F_ZERO_GRAMS


# --------------------------------------------------------------------------- #
# Grades
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("grams", "grade"),
    [
        (0.0, CarbonGrade.A_PLUS),
        (0.095, CarbonGrade.A_PLUS),  # Inclusive upper bound.
        (0.0951, CarbonGrade.A),
        (0.186, CarbonGrade.A),
        (0.1861, CarbonGrade.B),
        (0.341, CarbonGrade.B),
        (0.3411, CarbonGrade.C),
        (0.493, CarbonGrade.C),
        (0.4931, CarbonGrade.D),
        (0.656, CarbonGrade.D),
        (0.6561, CarbonGrade.E),
        (0.671, CarbonGrade.E),  # The demo site.
        (0.846, CarbonGrade.E),
        (0.8461, CarbonGrade.F),
        (3.0, CarbonGrade.F),
        (100.0, CarbonGrade.F),
    ],
)
def test_grade_for_grams(grams: float, grade: CarbonGrade) -> None:
    assert grade_for_grams(grams) is grade
    assert score_carbon(grams).grade is grade


# --------------------------------------------------------------------------- #
# Band boundaries: each band's edges must score its published endpoints
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("grams", "expected", "why"),
    [
        (0.0, 100, "a weightless page tops the A+ band"),
        (0.095, 95, "the A+ upper bound scores the bottom of 95-100"),
        (0.0951, 94, "a hair over drops into A at its top, 94"),
        (0.186, 85, "A lower edge"),
        (0.1861, 84, "top of B"),
        (0.341, 70, "B lower edge"),
        (0.3411, 69, "top of C"),
        (0.493, 55, "C lower edge"),
        (0.4931, 54, "top of D"),
        (0.656, 40, "D lower edge"),
        (0.6561, 39, "top of E"),
        (0.846, 25, "E lower edge"),
        (0.8461, 24, "top of F"),
        (3.0, 0, "MASTERSPEC §9.2 pins F at 0 by 3.0 g"),
        (10.0, 0, "and it stays at 0 beyond that"),
    ],
)
def test_band_boundaries(grams: float, expected: int, why: str) -> None:
    assert score_carbon(grams).score == expected, why


def test_boundaries_are_continuous() -> None:
    """Crossing a band edge must move the score by one point, not jump."""
    for grade in CARBON_BAND_ORDER[:-1]:
        edge = CARBON_BAND_MAX_GRAMS[grade]
        assert score_carbon(edge).score - score_carbon(edge + 1e-6).score == 1


# --------------------------------------------------------------------------- #
# Interpolation inside a band
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("grams", "expected", "why"),
    [
        (0.0475, 98, "halfway down A+: 100 - 0.5x5 = 97.5, rounded half up"),
        (0.1405, 90, "halfway down A: 94 - 0.5x9 = 89.5, rounded half up"),
        (0.2635, 77, "halfway down B: 84 - 0.5x14 = 77"),
        (1.923, 12, "halfway down F: 24 - 0.5x24 = 12"),
    ],
)
def test_interpolation(grams: float, expected: int, why: str) -> None:
    assert score_carbon(grams).score == expected, why


def test_score_never_rises_as_grams_rise() -> None:
    previous = 101
    grams = 0.0
    while grams <= 3.5:
        score = score_carbon(grams).score
        assert score <= previous, f"score rose at {grams} g"
        previous = score
        grams += 0.005


def test_negative_grams_are_treated_as_zero() -> None:
    """A negative estimate is nonsense, but must not produce a nonsense score."""
    assert score_carbon(-1.0).score == 100
    assert score_carbon(-1.0).grade is CarbonGrade.A_PLUS


# --------------------------------------------------------------------------- #
# Green hosting bonus
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("grams", "plain", "green"),
    [
        (0.671, 38, 41),
        (0.9, 23, 26),
        (2.0, 11, 14),
    ],
)
def test_green_bonus_adds_three(grams: float, plain: int, green: int) -> None:
    assert score_carbon(grams).score == plain
    assert score_carbon(grams, green=True).score == green
    assert green - plain == GREEN_HOST_BONUS


@pytest.mark.parametrize("grams", [0.0, 0.01, 0.03, 0.05])
def test_green_bonus_is_capped_at_100(grams: float) -> None:
    """A clean green page must not score 103."""
    raw = sum(item.points for item in score_carbon(grams, green=True).breakdown.items)
    assert raw > 100, "this case must actually hit the cap before rounding"
    assert score_carbon(grams, green=True).score == 100


def test_green_bonus_is_not_capped_before_it_has_to_be() -> None:
    """Just inside A+, the bonus still lands in full: 95.26 + 3 = 98."""
    assert score_carbon(0.09).score == 95
    assert score_carbon(0.09, green=True).score == 98


def test_green_bonus_does_not_change_the_grade() -> None:
    """A grade states what the page emits; the bonus is a score courtesy."""
    for grams in (0.05, 0.2, 0.7, 2.0):
        assert score_carbon(grams).grade is score_carbon(grams, green=True).grade


def test_green_bonus_appears_in_the_breakdown() -> None:
    labels = [item.label for item in score_carbon(0.5, green=True).breakdown.items]
    assert "Green hosting bonus" in labels
    assert "Green hosting bonus" not in [item.label for item in score_carbon(0.5).breakdown.items]


# --------------------------------------------------------------------------- #
# Breakdown and the model-level wrapper
# --------------------------------------------------------------------------- #


def test_breakdown_explains_the_band() -> None:
    breakdown = score_carbon(0.671).breakdown
    assert breakdown.spec_ref == "MASTERSPEC §9.2"
    assert breakdown.total == 38
    detail = breakdown.items[0].detail
    assert "estimate" in detail, "MASTERSPEC §7: carbon figures must be labelled"
    assert "0.671" in detail


def test_score_carbon_result_reads_the_models() -> None:
    carbon = CarbonResult(grams_per_view=0.671, assumptions=CarbonAssumptions())
    unknown = GreenResult(host="example.com", green=False, source=GreenSource.UNAVAILABLE)
    hosted = GreenResult(host="example.com", green=True, source=GreenSource.GREENWEB)

    assert score_carbon_result(carbon, unknown).score == 38
    assert score_carbon_result(carbon, hosted).score == 41


def test_unavailable_green_lookup_counts_as_not_green() -> None:
    """MASTERSPEC §7.2: a failed lookup is treated as not green."""
    carbon = CarbonResult(grams_per_view=0.5)
    unavailable = GreenResult(host="example.com", source=GreenSource.UNAVAILABLE)
    assert score_carbon_result(carbon, unavailable).score == score_carbon(0.5).score
