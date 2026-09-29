"""Assemble a :class:`~app.models.CarbonResult` (MASTERSPEC §5, §7).

Single responsibility: combine the network summary, the detector report and
the Sustainable Web Design model into the one ``CarbonResult`` the API serves.
Pure; no I/O.

Green hosting only changes the data-centre segment of the model. The pipeline
computes carbon before the Green Web Foundation answer arrives (MASTERSPEC §4
orders ``carbon`` before ``green``), so it builds the result with
``green=False`` and rebuilds it with :func:`build_carbon_result` once the host
is known to be green. The rebuild is cheap and deterministic.
"""

from __future__ import annotations

from app.carbon import swd
from app.carbon.constants import (
    FIRST_TIME_VIEWING_PERCENTAGE,
    GLOBAL_GRID_INTENSITY,
    KWH_PER_GB,
    PERCENTAGE_OF_DATA_LOADED_ON_SUBSEQUENT_LOAD,
    RENEWABLES_GRID_INTENSITY,
    RETURNING_VISITOR_PERCENTAGE,
    SWD_MODEL_VERSION,
    SWD_SOURCE_URL,
)
from app.carbon.detectors import DetectorReport
from app.models import CarbonAssumptions, CarbonResult
from app.scanner.network import NetworkSummary

__all__ = ["build_carbon_result", "carbon_assumptions"]


def carbon_assumptions(*, green: bool) -> CarbonAssumptions:
    """Every constant behind the gram figures, for the methodology popover."""
    return CarbonAssumptions(
        model="sustainable-web-design",
        model_version=SWD_MODEL_VERSION,
        kwh_per_gb=KWH_PER_GB,
        grid_intensity_g_per_kwh=GLOBAL_GRID_INTENSITY,
        renewable_intensity_g_per_kwh=RENEWABLES_GRID_INTENSITY,
        first_visit_percentage=FIRST_TIME_VIEWING_PERCENTAGE,
        return_visit_percentage=RETURNING_VISITOR_PERCENTAGE,
        data_reload_ratio=PERCENTAGE_OF_DATA_LOADED_ON_SUBSEQUENT_LOAD,
        green_hosted=green,
        segment_shares=swd.segment_shares(),
        source_url=SWD_SOURCE_URL,
    )


def build_carbon_result(
    summary: NetworkSummary,
    report: DetectorReport,
    *,
    green: bool,
) -> CarbonResult:
    """Combine measurements, detector output and the SWD estimate.

    * ``grams_first_visit``: a visitor with an empty cache downloads every byte.
    * ``grams_return_visit``: a returning visitor re-downloads 2% of them.
    * ``grams_per_view``: the model's weighted blend (75% first, 25% return),
      which is what the rating bands are calibrated against.
    """
    total = summary.total_bytes
    return CarbonResult(
        total_bytes=total,
        request_count=summary.request_count,
        by_type=dict(summary.by_type),
        third_party=summary.third_party.model_copy(deep=True),
        images=list(report.images),
        autoplay_media=list(report.autoplay_media),
        fonts=summary.fonts.model_copy(deep=True),
        uncompressed_text=list(summary.uncompressed_text),
        detections=list(report.detections),
        grams_per_view=swd.per_visit(total, green=green),
        grams_first_visit=swd.per_byte(total, green=green),
        grams_return_visit=swd.grams_return_visit(total, green=green),
        assumptions=carbon_assumptions(green=green),
    )
