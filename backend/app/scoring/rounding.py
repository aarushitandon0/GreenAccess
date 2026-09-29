"""Rounding rule shared by every score function.

Single responsibility: make "round to integer" (MASTERSPEC §9.1, §9.3) mean one
thing across the whole scoring package.

Python's built-in :func:`round` rounds half to even, so ``round(84.5)`` is 84
and ``round(85.5)`` is 86. A published score must not depend on the parity of
its neighbour, so the rule is written out here and used everywhere.
"""

from __future__ import annotations

import math

__all__ = ["round_half_up"]


def round_half_up(value: float) -> int:
    """Round to the nearest integer, halves going away from zero."""
    if value < 0:
        return -math.floor(-value + 0.5)
    return math.floor(value + 0.5)
