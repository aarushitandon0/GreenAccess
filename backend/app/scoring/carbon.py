"""Carbon score (MASTERSPEC §9.2).

Single responsibility: turn grams CO2e per view into a 0-100 score, a rating
grade, and the working behind both. Pure: no I/O, no clock, no randomness.

Method (MASTERSPEC §9.2, do not change without approval)

1. Place ``grams_per_view`` in a rating band. Bands are inclusive upper bounds
   in grams: A+ 0.095, A 0.186, B 0.341, C 0.493, D 0.656, E 0.846, F above.
2. Interpolate linearly inside the band. The band's dirty edge scores the
   band's lowest number, its clean edge the highest, so the mapping is
   continuous: 0.095 g scores exactly 95, and a hair more scores 94.
3. The F band has no upper gram bound, so it runs from 24 down to 0 at 3.0 g
   and stays at 0 above that.
4. A green-hosted domain adds 3 points, capped at 100.

The grams figure is itself an estimate from the Sustainable Web Design model
(MASTERSPEC §7.1), so the score inherits that. The grade is deliberately *not*
changed by the green-host bonus: a grade states what the page emits, and the
host's energy mix is already inside that number via :mod:`app.carbon.swd`.
"""

from __future__ import annotations

from app.models import (
    CarbonGrade,
    CarbonResult,
    CarbonScore,
    GreenResult,
    ScoreBreakdown,
    ScoreBreakdownItem,
)
from app.scoring.constants import (
    CARBON_BAND_MAX_GRAMS,
    CARBON_BAND_ORDER,
    CARBON_BAND_SCORES,
    CARBON_F_ZERO_GRAMS,
    CARBON_FORMULA,
    GREEN_HOST_BONUS,
    SCORE_MAX,
    SCORE_MIN,
)
from app.scoring.rounding import round_half_up

__all__ = ["band_bounds", "grade_for_grams", "score_carbon", "score_carbon_result"]


def grade_for_grams(grams: float) -> CarbonGrade:
    """The rating band `grams` falls in. Bands are inclusive upper bounds."""
    for grade in CARBON_BAND_ORDER:
        maximum = CARBON_BAND_MAX_GRAMS.get(grade)
        if maximum is None:  # F: everything left over.
            return grade
        if grams <= maximum:
            return grade
    return CarbonGrade.F


def band_bounds(grade: CarbonGrade) -> tuple[float, float]:
    """The (lower, upper) gram bounds of a band.

    The lower bound is the previous band's upper bound, so the bands tile the
    line with no gap. A+ starts at 0. F ends at the point MASTERSPEC §9.2 pins
    for a zero score.
    """
    index = CARBON_BAND_ORDER.index(grade)
    lower = 0.0 if index == 0 else CARBON_BAND_MAX_GRAMS[CARBON_BAND_ORDER[index - 1]]
    upper = CARBON_BAND_MAX_GRAMS.get(grade, CARBON_F_ZERO_GRAMS)
    return lower, upper


def _interpolate(grams: float, grade: CarbonGrade) -> float:
    """Position `grams` inside its band's score range, cleanest edge = highest."""
    worst, best = CARBON_BAND_SCORES[grade]
    lower, upper = band_bounds(grade)

    span = upper - lower
    if span <= 0:  # Unreachable with the pinned bands; guards against a bad edit.
        return float(best)

    # 0.0 at the clean edge of the band, 1.0 at the dirty edge.
    position = min(1.0, max(0.0, (grams - lower) / span))
    return best - position * (best - worst)


def score_carbon(grams_per_view: float, *, green: bool = False) -> CarbonScore:
    """Score grams CO2e per view, with a breakdown for the popover.

    `green` is the Green Web Foundation verdict. An unavailable lookup counts
    as not green (MASTERSPEC §7.2) and the UI shows it as "unknown".
    """
    grams = max(0.0, grams_per_view)
    grade = grade_for_grams(grams)
    base = _interpolate(grams, grade)

    worst, best = CARBON_BAND_SCORES[grade]
    lower, upper = band_bounds(grade)
    items = [
        ScoreBreakdownItem(
            label=f"Rating band {grade.value}",
            points=base,
            detail=(
                f"{grams:.3f} g CO2e per view (estimate) sits between "
                f"{lower:.3f} g and {upper:.3f} g, which scores {worst}-{best}"
            ),
        )
    ]

    total_raw = base
    if green:
        items.append(
            ScoreBreakdownItem(
                label="Green hosting bonus",
                points=float(GREEN_HOST_BONUS),
                detail=("Host reported as running on renewable energy by the Green Web Foundation"),
            )
        )
        total_raw += GREEN_HOST_BONUS

    score = max(SCORE_MIN, min(SCORE_MAX, round_half_up(total_raw)))

    return CarbonScore(
        score=score,
        grade=grade,
        breakdown=ScoreBreakdown(
            starting_points=0.0,
            items=items,
            total=score,
            formula=CARBON_FORMULA,
            spec_ref="MASTERSPEC §9.2",
        ),
    )


def score_carbon_result(carbon: CarbonResult, green: GreenResult) -> CarbonScore:
    """Convenience wrapper over the two scan sub-results the pipeline holds."""
    return score_carbon(carbon.grams_per_view, green=green.green)
