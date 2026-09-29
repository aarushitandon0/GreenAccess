"""Combined score and `compute_scores` assembly tests (MASTERSPEC §9.3, §15)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import (
    A11yResult,
    CarbonGrade,
    CarbonResult,
    GreenResult,
    GreenSource,
    Impact,
    KeyboardResult,
    Violation,
    ViolationNode,
    Weights,
)
from app.scoring.combined import compute_scores, score_combined
from app.scoring.constants import WEIGHT_UI_MAX, WEIGHT_UI_MIN
from app.scoring.rounding import round_half_up

# --------------------------------------------------------------------------- #
# Rounding rule
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, 0),
        (0.4, 0),
        (0.5, 1),  # Half up, not Python's half-to-even.
        (1.5, 2),
        (2.5, 3),
        (84.5, 85),
        (-0.5, -1),
        (-1.5, -2),
    ],
)
def test_round_half_up(value: float, expected: int) -> None:
    assert round_half_up(value) == expected


# --------------------------------------------------------------------------- #
# score_combined
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("a11y", "carbon", "weights", "expected", "why"),
    [
        (100, 100, None, 100, "default 0.5/0.5 on a perfect page"),
        (0, 0, None, 0, "and on the worst possible one"),
        (40, 38, None, 39, "0.5x40 + 0.5x38"),
        (85, 38, None, 62, "61.5 rounds half up to 62"),
        (85, 38, Weights(a11y=0.7, carbon=0.3), 71, "0.7x85 + 0.3x38 = 70.9"),
        (85, 38, Weights(a11y=0.3, carbon=0.7), 52, "0.3x85 + 0.7x38 = 52.1"),
        (85, 38, Weights(a11y=1.0, carbon=0.0), 85, "weight extreme: accessibility only"),
        (85, 38, Weights(a11y=0.0, carbon=1.0), 38, "weight extreme: carbon only"),
    ],
)
def test_score_combined(
    a11y: int, carbon: int, weights: Weights | None, expected: int, why: str
) -> None:
    assert score_combined(a11y, carbon, weights).score == expected, why


def test_default_weights_are_an_even_split() -> None:
    default = Weights()
    assert (default.a11y, default.carbon) == (0.5, 0.5)


def test_weights_must_sum_to_one() -> None:
    with pytest.raises(ValidationError):
        Weights(a11y=0.7, carbon=0.7)


def test_ui_weight_range_matches_spec() -> None:
    """MASTERSPEC §9.3: the UI toggle spans 0.3-0.7."""
    assert (WEIGHT_UI_MIN, WEIGHT_UI_MAX) == (0.3, 0.7)


def test_combined_breakdown_shows_both_sides() -> None:
    breakdown = score_combined(80, 40, Weights(a11y=0.7, carbon=0.3)).breakdown
    assert [item.label for item in breakdown.items] == ["Accessibility", "Carbon"]
    assert breakdown.items[0].points == pytest.approx(56.0)
    assert breakdown.items[1].points == pytest.approx(12.0)
    assert breakdown.total == 68
    assert breakdown.spec_ref == "MASTERSPEC §9.3"


def test_combined_breakdown_leads_with_the_bigger_contributor() -> None:
    items = score_combined(10, 90).breakdown.items
    assert items[0].label == "Carbon"


# --------------------------------------------------------------------------- #
# compute_scores: the `score` pipeline step
# --------------------------------------------------------------------------- #


def _scan_inputs() -> dict[str, object]:
    return {
        "a11y": A11yResult(
            violations=[
                Violation(
                    rule_id="image-alt",
                    impact=Impact.CRITICAL,
                    help="Images must have alternate text",
                    nodes=[ViolationNode(selector="img.card", html="<img>")],
                )
            ],
            unique_rules=1,
            total_nodes=1,
        ),
        "keyboard": KeyboardResult(trap_detected=True, trap_container="#promo"),
        "carbon": CarbonResult(grams_per_view=0.671),
        "green": GreenResult(host="example.com", source=GreenSource.UNAVAILABLE),
    }


def test_compute_scores_assembles_all_three() -> None:
    scores = compute_scores(**_scan_inputs())
    assert scores.a11y == 80  # 100 - 10 (critical, 1 node) - 10 (trap)
    assert scores.carbon == 38
    assert scores.carbon_grade is CarbonGrade.E
    assert scores.combined == 59  # 0.5x80 + 0.5x38 = 59


def test_compute_scores_is_never_a_placeholder() -> None:
    """A Scores built from real findings must not be mistaken for a stub."""
    assert compute_scores(**_scan_inputs()).is_placeholder is False


def test_compute_scores_attaches_every_breakdown() -> None:
    breakdown = compute_scores(**_scan_inputs()).breakdown
    assert breakdown is not None
    assert breakdown.a11y.spec_ref == "MASTERSPEC §9.1"
    assert breakdown.carbon.spec_ref == "MASTERSPEC §9.2"
    assert breakdown.combined.spec_ref == "MASTERSPEC §9.3"
    assert breakdown.a11y.total == 80


def test_compute_scores_records_the_weights_it_used() -> None:
    weights = Weights(a11y=0.7, carbon=0.3)
    scores = compute_scores(**_scan_inputs(), weights=weights)
    assert scores.weights == weights
    assert scores.combined == 67  # 0.7x80 + 0.3x38 = 67.4


def test_compute_scores_is_deterministic() -> None:
    first = compute_scores(**_scan_inputs())
    second = compute_scores(**_scan_inputs())
    assert first.model_dump() == second.model_dump()
