"""Accessibility score tests (MASTERSPEC §9.1, §15).

Table-driven. Every expected number below is worked out by hand from the
formula in MASTERSPEC §9.1 and written as arithmetic in the table, so a test
failure says which part of the formula moved rather than just "85 != 84".
"""

from __future__ import annotations

import math

import pytest

from app.models import A11yResult, Impact, KeyboardResult, Violation, ViolationNode
from app.scoring.a11y import node_multiplier, rule_penalty, score_a11y
from app.scoring.constants import IMPACT_BASE, KEYBOARD_TRAP_PENALTY


def _violation(rule_id: str, impact: Impact, nodes: int) -> Violation:
    return Violation(
        rule_id=rule_id,
        impact=impact,
        help=f"{rule_id} help",
        nodes=[
            ViolationNode(selector=f"#{rule_id}-{index}", html="<p></p>") for index in range(nodes)
        ],
    )


def _result(*violations: Violation, incomplete: int = 0) -> A11yResult:
    return A11yResult(
        violations=list(violations),
        unique_rules=len(violations),
        total_nodes=sum(len(v.nodes) for v in violations),
        incomplete_count=incomplete,
    )


# --------------------------------------------------------------------------- #
# The node multiplier: min(1 + log10(nodes), 2)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("nodes", "expected"),
    [
        (0, 1.0),  # No recorded nodes still counts as one occurrence.
        (1, 1.0),  # 1 + log10(1)
        (2, 1 + math.log10(2)),
        (10, 2.0),  # 1 + log10(10) lands exactly on the cap.
        (11, 2.0),  # Capped.
        (100, 2.0),
        (500, 2.0),  # The cap holds no matter how many nodes.
    ],
)
def test_node_multiplier(nodes: int, expected: float) -> None:
    assert node_multiplier(nodes) == pytest.approx(expected)


def test_node_multiplier_never_exceeds_two() -> None:
    for nodes in range(1, 2001):
        assert node_multiplier(nodes) <= 2.0


# --------------------------------------------------------------------------- #
# Per-rule penalties: base[impact] x multiplier
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("impact", "base"),
    [
        (Impact.CRITICAL, 10),
        (Impact.SERIOUS, 7),
        (Impact.MODERATE, 3),
        (Impact.MINOR, 1),
    ],
)
def test_impact_bases_match_spec(impact: Impact, base: int) -> None:
    """MASTERSPEC §9.1: base = {critical: 10, serious: 7, moderate: 3, minor: 1}."""
    assert IMPACT_BASE[impact] == base
    assert rule_penalty(_violation("r", impact, 1)) == pytest.approx(base)


@pytest.mark.parametrize(
    ("impact", "nodes", "expected"),
    [
        (Impact.CRITICAL, 1, 10.0),
        (Impact.CRITICAL, 10, 20.0),
        (Impact.CRITICAL, 500, 20.0),  # Cap holds.
        (Impact.SERIOUS, 2, 7 * (1 + math.log10(2))),
        (Impact.MODERATE, 4, 3 * (1 + math.log10(4))),
        (Impact.MINOR, 1000, 2.0),
    ],
)
def test_rule_penalty(impact: Impact, nodes: int, expected: float) -> None:
    assert rule_penalty(_violation("r", impact, nodes)) == pytest.approx(expected)


# --------------------------------------------------------------------------- #
# The score itself
# --------------------------------------------------------------------------- #


def test_no_violations_scores_100() -> None:
    scored = score_a11y(_result(), KeyboardResult())
    assert scored.score == 100
    assert scored.breakdown.items == []
    assert scored.breakdown.starting_points == 100.0


def test_incomplete_items_are_not_scored() -> None:
    """MASTERSPEC §6.3: incomplete is recorded but never scored."""
    assert score_a11y(_result(incomplete=42), KeyboardResult()).score == 100


@pytest.mark.parametrize(
    ("violations", "keyboard", "expected", "why"),
    [
        (
            [("a", Impact.CRITICAL, 1)],
            KeyboardResult(),
            90,
            "100 - 10x1.00",
        ),
        (
            [("a", Impact.CRITICAL, 500)],
            KeyboardResult(),
            80,
            "100 - 10x2.00, the node cap holding at 500 nodes",
        ),
        (
            [("a", Impact.CRITICAL, 10)],
            KeyboardResult(),
            80,
            "10 nodes reaches the cap exactly, so it matches 500 nodes",
        ),
        (
            [("a", Impact.SERIOUS, 2)],
            KeyboardResult(),
            91,
            "100 - 7x1.30103 = 90.893, rounded half up",
        ),
        (
            [],
            KeyboardResult(trap_detected=True, trap_container="#promo"),
            90,
            "100 - 10 for the keyboard trap alone",
        ),
        (
            [("a", Impact.CRITICAL, 1)],
            KeyboardResult(trap_detected=True, trap_container="#promo"),
            80,
            "100 - 10 - 10: the trap stacks with axe violations",
        ),
        (
            [("a", Impact.MINOR, 1), ("b", Impact.MODERATE, 1)],
            KeyboardResult(),
            96,
            "100 - 1 - 3",
        ),
        (
            [(f"r{i}", Impact.CRITICAL, 10) for i in range(6)],
            KeyboardResult(),
            0,
            "6 x 20 = 120 of penalty clamps at the floor, never negative",
        ),
    ],
)
def test_score_table(
    violations: list[tuple[str, Impact, int]],
    keyboard: KeyboardResult,
    expected: int,
    why: str,
) -> None:
    result = _result(*[_violation(*spec) for spec in violations])
    assert score_a11y(result, keyboard).score == expected, why


def test_score_is_never_negative() -> None:
    result = _result(*[_violation(f"r{i}", Impact.CRITICAL, 100) for i in range(50)])
    assert score_a11y(result, KeyboardResult()).score == 0


def test_keyboard_argument_is_optional() -> None:
    assert score_a11y(_result()).score == 100


def test_trap_penalty_matches_constant() -> None:
    trapped = KeyboardResult(trap_detected=True, trap_container="#promo")
    assert 100 - score_a11y(_result(), trapped).score == KEYBOARD_TRAP_PENALTY


# --------------------------------------------------------------------------- #
# Breakdown (MASTERSPEC §9, "How is this calculated?")
# --------------------------------------------------------------------------- #


def test_breakdown_lists_every_rule_and_the_trap() -> None:
    result = _result(
        _violation("color-contrast", Impact.SERIOUS, 3),
        _violation("image-alt", Impact.CRITICAL, 7),
    )
    keyboard = KeyboardResult(trap_detected=True, trap_container="#promo")
    breakdown = score_a11y(result, keyboard).breakdown

    labels = [item.label for item in breakdown.items]
    assert set(labels) == {"color-contrast", "image-alt", "keyboard-trap"}
    assert breakdown.total == score_a11y(result, keyboard).score
    assert breakdown.spec_ref == "MASTERSPEC §9.1"
    assert breakdown.formula


def test_breakdown_is_ordered_worst_first() -> None:
    result = _result(
        _violation("minor-thing", Impact.MINOR, 1),
        _violation("image-alt", Impact.CRITICAL, 7),
        _violation("label", Impact.SERIOUS, 2),
    )
    items = score_a11y(result, KeyboardResult()).breakdown.items
    assert [item.label for item in items] == ["image-alt", "label", "minor-thing"]
    assert all(item.points < 0 for item in items)


def test_breakdown_adds_up_to_the_score() -> None:
    result = _result(
        _violation("image-alt", Impact.CRITICAL, 7),
        _violation("label", Impact.SERIOUS, 4),
    )
    scored = score_a11y(result, KeyboardResult(trap_detected=True))
    raw = scored.breakdown.starting_points + sum(i.points for i in scored.breakdown.items)
    assert scored.score == pytest.approx(raw, abs=0.5)


def test_breakdown_carries_evidence_selectors() -> None:
    result = _result(_violation("image-alt", Impact.CRITICAL, 7))
    item = score_a11y(result, KeyboardResult()).breakdown.items[0]
    assert item.evidence == ["#image-alt-0", "#image-alt-1", "#image-alt-2"]


def test_trap_breakdown_attributes_the_crawl_not_axe() -> None:
    """MASTERSPEC §6.4: traps are ours, not axe's, and must say so."""
    keyboard = KeyboardResult(trap_detected=True, trap_container="#promo")
    item = next(
        i for i in score_a11y(_result(), keyboard).breakdown.items if i.label == "keyboard-trap"
    )
    assert "keyboard crawl" in item.detail
    assert item.evidence == ["#promo"]
