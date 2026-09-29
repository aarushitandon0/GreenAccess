"""Pure score functions (MASTERSPEC §9).

Nothing in this package performs I/O. Every function is deterministic and
table-tested, so any score can be re-derived from a stored `ScanResult`.
"""

from app.scoring.a11y import score_a11y
from app.scoring.carbon import grade_for_grams, score_carbon, score_carbon_result
from app.scoring.combined import compute_scores, score_combined

__all__ = [
    "compute_scores",
    "grade_for_grams",
    "score_a11y",
    "score_carbon",
    "score_carbon_result",
    "score_combined",
]
