"""Accessibility score (MASTERSPEC §9.1).

Single responsibility: turn an :class:`~app.models.A11yResult` and a
:class:`~app.models.KeyboardResult` into a 0-100 score plus the working behind
it. Pure: no I/O, no clock, no randomness, same input -> same output.

Formula (MASTERSPEC §9.1, do not change without approval)::

    score   = max(0, 100 - sum(penalty per unique violated rule))
    penalty = base[impact] * min(1 + log10(nodes), 2)
    base    = {critical: 10, serious: 7, moderate: 3, minor: 1}

plus a flat 10 if the keyboard crawl found a trap. Items axe reports as
`incomplete` (needs review) are recorded by the scanner but never scored.
"""

from __future__ import annotations

import math

from app.models import (
    A11yResult,
    A11yScore,
    KeyboardResult,
    ScoreBreakdown,
    ScoreBreakdownItem,
    Violation,
)
from app.scoring.constants import (
    A11Y_FORMULA,
    A11Y_STARTING_POINTS,
    IMPACT_BASE,
    KEYBOARD_TRAP_PENALTY,
    NODE_MULTIPLIER_CAP,
    NODE_MULTIPLIER_FLOOR,
    SCORE_MAX,
    SCORE_MIN,
)
from app.scoring.rounding import round_half_up

__all__ = ["node_multiplier", "rule_penalty", "score_a11y"]

#: How many evidence selectors to attach to a breakdown item. The popover shows
#: a handful; the full node list lives on the violation itself.
_MAX_EVIDENCE = 3


def node_multiplier(nodes: int) -> float:
    """``min(1 + log10(nodes), 2)``, the repeat-offender multiplier.

    A rule that failed once costs its base; ten or more failing nodes cost
    exactly twice the base and no more. ``nodes`` below 1 is treated as 1: a
    violation with no recorded nodes still happened, and ``log10(0)`` has no
    value to fall back on.
    """
    effective = max(1, nodes)
    raw = NODE_MULTIPLIER_FLOOR + math.log10(effective)
    return min(raw, NODE_MULTIPLIER_CAP)


def rule_penalty(violation: Violation) -> float:
    """Points deducted for one unique violated rule."""
    return IMPACT_BASE[violation.impact] * node_multiplier(len(violation.nodes))


def score_a11y(a11y: A11yResult, keyboard: KeyboardResult | None = None) -> A11yScore:
    """Score the accessibility findings, with a breakdown for the popover.

    `keyboard` is optional so the function can be scored on axe output alone;
    omitting it simply means no trap penalty, never a silent zero.
    """
    items: list[ScoreBreakdownItem] = []

    for violation in a11y.violations:
        nodes = len(violation.nodes)
        penalty = rule_penalty(violation)
        items.append(
            ScoreBreakdownItem(
                label=violation.rule_id,
                points=-penalty,
                detail=(
                    f"{violation.impact.value} impact, {nodes} "
                    f"{'element' if nodes == 1 else 'elements'}: "
                    f"{IMPACT_BASE[violation.impact]} x "
                    f"{node_multiplier(nodes):.2f}"
                ),
                evidence=[node.selector for node in violation.nodes[:_MAX_EVIDENCE]],
            )
        )

    if keyboard is not None and keyboard.trap_detected:
        container = keyboard.trap_container
        items.append(
            ScoreBreakdownItem(
                label="keyboard-trap",
                points=-float(KEYBOARD_TRAP_PENALTY),
                # Attribution matters: this is our Tab crawl, not axe (§6.4).
                detail=(
                    "Focus could not be moved past this container, detected by "
                    "automated keyboard crawl"
                ),
                evidence=[container] if container else [],
            )
        )

    # Heaviest penalty first, so the popover leads with what actually matters.
    items.sort(key=lambda item: item.points)

    raw = A11Y_STARTING_POINTS + sum(item.points for item in items)
    score = max(SCORE_MIN, min(SCORE_MAX, round_half_up(raw)))

    return A11yScore(
        score=score,
        breakdown=ScoreBreakdown(
            starting_points=float(A11Y_STARTING_POINTS),
            items=items,
            total=score,
            formula=A11Y_FORMULA,
            spec_ref="MASTERSPEC §9.1",
        ),
    )
