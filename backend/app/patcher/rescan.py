"""Make the re-scan of a patched copy comparable with the original scan.

Single responsibility: correct the one thing the patch preview changes that
the patch itself does not: where the page is hosted.

The patched copy is served by GreenAccess, not by the site's own host, so the
re-scan's Green Web Foundation lookup describes GreenAccess's server. A patch
cannot change a site's hosting, so the "after" result keeps the original
host's green status. Grams and scores are then recomputed with the same pure
functions the pipeline uses (MASTERSPEC §7.1, §9); every measured number (bytes,
requests, violations, keyboard crawl) is left exactly as the re-scan found it.
"""

from __future__ import annotations

from app.carbon import swd
from app.models import GreenResult, ScanResult, Weights
from app.scoring.combined import compute_scores
from app.tradeoffs.engine import evaluate as evaluate_tradeoffs

__all__ = ["carry_hosting"]


def carry_hosting(after: ScanResult, original: GreenResult, weights: Weights) -> ScanResult:
    """`after` re-scored as if served from the original host (green status only)."""
    if after.green == original:
        return after
    green = original.green
    total = after.carbon.total_bytes
    carbon = after.carbon.model_copy(
        update={
            "grams_per_view": swd.per_visit(total, green=green),
            "grams_first_visit": swd.per_byte(total, green=green),
            "grams_return_visit": swd.grams_return_visit(total, green=green),
            "assumptions": after.carbon.assumptions.model_copy(update={"green_hosted": green}),
        }
    )
    result = after.model_copy(update={"carbon": carbon, "green": original})
    result.scores = compute_scores(
        a11y=result.a11y,
        keyboard=result.keyboard,
        carbon=carbon,
        green=original,
        weights=weights,
    )
    result.tradeoffs = evaluate_tradeoffs(result)
    return result
