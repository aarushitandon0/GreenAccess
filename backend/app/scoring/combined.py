"""Combined score (MASTERSPEC §9.3) and the assembler for :class:`Scores`.

Single responsibility: weight the accessibility and carbon scores into one
number, and build the :class:`~app.models.Scores` object the API serves. Pure:
no I/O, no clock, no randomness.

``combined = round(w_a11y * a11y + w_carbon * carbon)``, default 0.5/0.5. The
UI toggle spans 0.3-0.7 (:data:`~app.scoring.constants.WEIGHT_UI_MIN`), but
this function honours whatever weights it is handed so the extremes stay
unit-testable; clamping the toggle is the API's job.
"""

from __future__ import annotations

from app.models import (
    A11yResult,
    CarbonResult,
    CombinedScore,
    GreenResult,
    KeyboardResult,
    ScoreBreakdown,
    ScoreBreakdownItem,
    Scores,
    ScoresBreakdown,
    Weights,
)
from app.scoring.a11y import score_a11y
from app.scoring.carbon import score_carbon_result
from app.scoring.constants import COMBINED_FORMULA, SCORE_MAX, SCORE_MIN
from app.scoring.rounding import round_half_up

__all__ = ["compute_scores", "score_combined"]


def score_combined(
    a11y_score: int,
    carbon_score: int,
    weights: Weights | None = None,
) -> CombinedScore:
    """Weighted average of the two scores, with a breakdown for the popover."""
    resolved = weights or Weights()

    a11y_points = resolved.a11y * a11y_score
    carbon_points = resolved.carbon * carbon_score

    items = [
        ScoreBreakdownItem(
            label="Accessibility",
            points=a11y_points,
            detail=f"{a11y_score} x {resolved.a11y:.2f} weight",
        ),
        ScoreBreakdownItem(
            label="Carbon",
            points=carbon_points,
            detail=f"{carbon_score} x {resolved.carbon:.2f} weight",
        ),
    ]
    # Largest contributor first, matching the other two breakdowns.
    items.sort(key=lambda item: item.points, reverse=True)

    score = max(SCORE_MIN, min(SCORE_MAX, round_half_up(a11y_points + carbon_points)))

    return CombinedScore(
        score=score,
        breakdown=ScoreBreakdown(
            starting_points=0.0,
            items=items,
            total=score,
            formula=COMBINED_FORMULA,
            spec_ref="MASTERSPEC §9.3",
        ),
    )


def compute_scores(
    *,
    a11y: A11yResult,
    keyboard: KeyboardResult,
    carbon: CarbonResult,
    green: GreenResult,
    weights: Weights | None = None,
) -> Scores:
    """Run all three score functions and assemble the API-facing `Scores`.

    This is the `score` pipeline step (MASTERSPEC §4). ``is_placeholder`` is
    False here and only here: a `Scores` built by this function was computed
    from real findings.
    """
    resolved = weights or Weights()

    a11y_score = score_a11y(a11y, keyboard)
    carbon_score = score_carbon_result(carbon, green)
    combined = score_combined(a11y_score.score, carbon_score.score, resolved)

    return Scores(
        a11y=a11y_score.score,
        carbon=carbon_score.score,
        combined=combined.score,
        carbon_grade=carbon_score.grade,
        weights=resolved,
        is_placeholder=False,
        breakdown=ScoresBreakdown(
            a11y=a11y_score.breakdown,
            carbon=carbon_score.breakdown,
            combined=combined.breakdown,
        ),
    )
