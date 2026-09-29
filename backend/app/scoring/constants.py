"""Every constant behind the three scores (MASTERSPEC §9).

Single responsibility: hold the scoring numbers, with a provenance note per
block. No logic lives here, and no other module may hard-code these values.

Provenance and verification status
----------------------------------
**Impact bases, node cap, trap penalty, score ranges, green bonus, weights**
  Defined by MASTERSPEC §9 itself. They are GreenAccess's own design, not a
  third-party standard, so there is nothing external to verify them against.
  Status: SPEC-DEFINED.

**Carbon rating bands (grams per view)**
  MASTERSPEC §9.2 asks these to be verified against websitecarbon.com's
  published ratings.

  * websitecarbon.com/how-does-it-work and sustainablewebdesign.org's digital
    carbon ratings page both returned **HTTP 403** on 2026-09-29, so the
    published pages could not be read. Status against that source:
    **UNVERIFIED**.
  * They were instead verified against the pinned upstream implementation that
    those pages describe: ``@tgwf/co2`` **0.19.0**,
    ``dist/esm/constants/index.js`` → ``SWDMV3_RATINGS``, which is vendored in
    this repo at ``scripts/node_modules/@tgwf/co2``. Every one of the six
    thresholds matches MASTERSPEC §9.2 exactly. Status against the pinned
    source: **VERIFIED, 2026-09-29**.

  The band edges are duplicated in :mod:`app.carbon.constants` as
  ``SWDM_V3_RATINGS`` (the grade lookup used by the SWD port). A test asserts
  the two tables agree, so they cannot drift apart.
"""

from __future__ import annotations

from typing import Final

from app.models import CarbonGrade, Impact

# --------------------------------------------------------------------------- #
# Accessibility (MASTERSPEC §9.1)
#
#   score = max(0, 100 - sum(penalty_rule))
#   penalty_rule = base[impact] * min(1 + log10(nodes), 2)
# --------------------------------------------------------------------------- #

A11Y_STARTING_POINTS: Final[int] = 100

#: Penalty weight per axe impact level. SPEC-DEFINED (MASTERSPEC §9.1).
IMPACT_BASE: Final[dict[Impact, int]] = {
    Impact.CRITICAL: 10,
    Impact.SERIOUS: 7,
    Impact.MODERATE: 3,
    Impact.MINOR: 1,
}

#: Upper bound on the node multiplier. `1 + log10(nodes)` reaches 2.0 at 10
#: nodes, so a rule violated 10 times and one violated 500 times cost the same.
#: Deliberate: it stops a single repeated rule from consuming the whole score.
NODE_MULTIPLIER_CAP: Final[float] = 2.0

#: Floor on the node multiplier. `nodes = 1` gives `1 + log10(1) = 1.0`.
NODE_MULTIPLIER_FLOOR: Final[float] = 1.0

#: Flat penalty for a keyboard trap, found by our own Tab crawl, not by axe
#: (MASTERSPEC §6.4, §9.1). Equal to one critical rule at a single node.
KEYBOARD_TRAP_PENALTY: Final[int] = 10

# --------------------------------------------------------------------------- #
# Carbon (MASTERSPEC §9.2)
# --------------------------------------------------------------------------- #

#: Inclusive upper bound in grams CO2e per view for each band. Anything above
#: the E bound is F. VERIFIED against @tgwf/co2 0.19.0 SWDMV3_RATINGS.
CARBON_BAND_MAX_GRAMS: Final[dict[CarbonGrade, float]] = {
    CarbonGrade.A_PLUS: 0.095,
    CarbonGrade.A: 0.186,
    CarbonGrade.B: 0.341,
    CarbonGrade.C: 0.493,
    CarbonGrade.D: 0.656,
    CarbonGrade.E: 0.846,
}

#: Score range per band, as (worst, best). Interpolated linearly across the
#: band: the band's upper gram bound scores `worst`, its lower bound `best`.
#: SPEC-DEFINED (MASTERSPEC §9.2).
CARBON_BAND_SCORES: Final[dict[CarbonGrade, tuple[int, int]]] = {
    CarbonGrade.A_PLUS: (95, 100),
    CarbonGrade.A: (85, 94),
    CarbonGrade.B: (70, 84),
    CarbonGrade.C: (55, 69),
    CarbonGrade.D: (40, 54),
    CarbonGrade.E: (25, 39),
    CarbonGrade.F: (0, 24),
}

#: Bands from cleanest to dirtiest. Iteration order matters for band lookup.
CARBON_BAND_ORDER: Final[tuple[CarbonGrade, ...]] = (
    CarbonGrade.A_PLUS,
    CarbonGrade.A,
    CarbonGrade.B,
    CarbonGrade.C,
    CarbonGrade.D,
    CarbonGrade.E,
    CarbonGrade.F,
)

#: The F band has no upper gram bound, so MASTERSPEC §9.2 pins the point at
#: which it bottoms out: "F 24 -> 0 (0 at 3.0 g)". Beyond this the score is 0.
CARBON_F_ZERO_GRAMS: Final[float] = 3.0

#: Added to the carbon score when the Green Web Foundation reports a green
#: host (MASTERSPEC §9.2). Applied after interpolation, then capped.
GREEN_HOST_BONUS: Final[int] = 3

SCORE_MIN: Final[int] = 0
SCORE_MAX: Final[int] = 100

# --------------------------------------------------------------------------- #
# Combined (MASTERSPEC §9.3)
# --------------------------------------------------------------------------- #

DEFAULT_A11Y_WEIGHT: Final[float] = 0.5
DEFAULT_CARBON_WEIGHT: Final[float] = 0.5

#: The range the UI's weight toggle may span (MASTERSPEC §9.3). Enforced by the
#: API, not by the pure score function: the function honours whatever it is
#: given so that the extremes stay unit-testable.
WEIGHT_UI_MIN: Final[float] = 0.3
WEIGHT_UI_MAX: Final[float] = 0.7

# --------------------------------------------------------------------------- #
# Popover copy (MASTERSPEC §9: "How is this calculated?")
# --------------------------------------------------------------------------- #

A11Y_FORMULA: Final[str] = (
    "100 minus one penalty per failing rule, where penalty = "
    "base(impact) x min(1 + log10(nodes), 2), plus 10 if the keyboard crawl "
    "found a trap. Items axe marks as needing review are not scored."
)
CARBON_FORMULA: Final[str] = (
    "Grams CO2e per view (Sustainable Web Design v3, an estimate) placed in a "
    "rating band, then interpolated linearly inside that band, plus 3 for a "
    "green-hosted domain, capped at 100."
)
COMBINED_FORMULA: Final[str] = (
    "A weighted average of the accessibility and carbon scores, rounded to a whole number."
)

__all__ = [
    "A11Y_FORMULA",
    "A11Y_STARTING_POINTS",
    "CARBON_BAND_MAX_GRAMS",
    "CARBON_BAND_ORDER",
    "CARBON_BAND_SCORES",
    "CARBON_FORMULA",
    "CARBON_F_ZERO_GRAMS",
    "COMBINED_FORMULA",
    "DEFAULT_A11Y_WEIGHT",
    "DEFAULT_CARBON_WEIGHT",
    "GREEN_HOST_BONUS",
    "IMPACT_BASE",
    "KEYBOARD_TRAP_PENALTY",
    "NODE_MULTIPLIER_CAP",
    "NODE_MULTIPLIER_FLOOR",
    "SCORE_MAX",
    "SCORE_MIN",
    "WEIGHT_UI_MAX",
    "WEIGHT_UI_MIN",
]
