"""Sustainable Web Design model constants.

MASTERSPEC §7.1: **do not write these from memory.** Every value below was
copied from the pinned CO2.js source, and each block records where.

Source of truth
---------------
Package : ``@tgwf/co2`` version **0.19.0** (npm)
Files   : ``dist/esm/constants/index.js``
          ``dist/esm/constants/file-size.js``
          ``dist/esm/data/average-intensities.min.js``
          ``dist/esm/sustainable-web-design-v3.js``
          ``dist/esm/helpers/index.js``
Docs    : https://developers.thegreenwebfoundation.org/co2js/models/overview/
Model   : https://sustainablewebdesign.org/estimating-digital-emissions/
Copied  : 2026-09-29

Why version 3 and not version 4
-------------------------------
CO2.js defaults to SWD **v4**. We pin **v3**, deliberately.

The rating bands in MASTERSPEC §9.2 (0.095 / 0.186 / 0.341 / 0.493 / 0.656 /
0.846) are exactly CO2.js's ``SWDMV3_RATINGS``. The v4 bands are roughly 2.4x
lower (0.04 / 0.079 / 0.145 / 0.209 / 0.278 / 0.359). Feeding a v4 gram figure
into v3 bands would grade almost any page A+, because v4 reports a much smaller
number for the same byte count.

So the model and the bands are pinned together as a matched pair. The v4
constants are recorded below for reference and for a future migration, but
nothing uses them: changing model version means changing bands in the same
commit.

Units
-----
* Byte counts are transfer bytes.
* ``GIGABYTE`` is decimal (10^9), not 2^30. CO2.js uses 1e9; using 2^30 would
  make every figure about 7% low.
* Grid intensity is grams CO2e per kWh.
"""

from __future__ import annotations

from typing import Final

# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #

CO2JS_VERSION: Final[str] = "0.19.0"
SWD_MODEL_VERSION: Final[str] = "3"
SWD_SOURCE_URL: Final[str] = "https://sustainablewebdesign.org/estimating-digital-emissions/"
CO2JS_SOURCE_URL: Final[str] = "https://developers.thegreenwebfoundation.org/co2js/"

# --------------------------------------------------------------------------- #
# Units
# --------------------------------------------------------------------------- #

# co2.js dist/esm/constants/file-size.js:
#   const GIGABYTE = 1e3 * 1e3 * 1e3;
GIGABYTE: Final[int] = 1_000_000_000

# --------------------------------------------------------------------------- #
# SWD v3 energy model
# co2.js dist/esm/constants/index.js
# --------------------------------------------------------------------------- #

# const KWH_PER_GB = 0.81;
KWH_PER_GB: Final[float] = 0.81

# Shares of total system energy. These four sum to 1.0.
#   const END_USER_DEVICE_ENERGY = 0.52;
#   const NETWORK_ENERGY         = 0.14;
#   const DATACENTER_ENERGY      = 0.15;
#   const PRODUCTION_ENERGY      = 0.19;
END_USER_DEVICE_ENERGY: Final[float] = 0.52
NETWORK_ENERGY: Final[float] = 0.14
DATACENTER_ENERGY: Final[float] = 0.15
PRODUCTION_ENERGY: Final[float] = 0.19

# const GLOBAL_GRID_INTENSITY = averageIntensity.data["WORLD"];
# dist/esm/data/average-intensities.min.js: "WORLD": 472.94
GLOBAL_GRID_INTENSITY: Final[float] = 472.94

# const RENEWABLES_GRID_INTENSITY = 50;
# Applied to the data-centre segment only, and only when the host is green.
RENEWABLES_GRID_INTENSITY: Final[float] = 50.0

# --------------------------------------------------------------------------- #
# Caching / visitor assumptions (the perVisit path)
# co2.js dist/esm/constants/index.js
# --------------------------------------------------------------------------- #

# const FIRST_TIME_VIEWING_PERCENTAGE = 0.75;
FIRST_TIME_VIEWING_PERCENTAGE: Final[float] = 0.75

# const RETURNING_VISITOR_PERCENTAGE = 0.25;
RETURNING_VISITOR_PERCENTAGE: Final[float] = 0.25

# const PERCENTAGE_OF_DATA_LOADED_ON_SUBSEQUENT_LOAD = 0.02;
# A returning visitor re-downloads 2% of the page; the rest is cached.
PERCENTAGE_OF_DATA_LOADED_ON_SUBSEQUENT_LOAD: Final[float] = 0.02

# --------------------------------------------------------------------------- #
# Rating bands
# co2.js dist/esm/constants/index.js -> SWDMV3_RATINGS
# Applied to the perVisit gram figure by helpers/outputRating.
# These match MASTERSPEC §9.2 exactly. VERIFIED against the pinned source.
# --------------------------------------------------------------------------- #

SWDM_V3_RATINGS: Final[dict[str, float]] = {
    "A+": 0.095,  # FIFTH_PERCENTILE
    "A": 0.186,  # TENTH_PERCENTILE
    "B": 0.341,  # TWENTIETH_PERCENTILE
    "C": 0.493,  # THIRTIETH_PERCENTILE
    "D": 0.656,  # FORTIETH_PERCENTILE
    "E": 0.846,  # FIFTIETH_PERCENTILE
    # Anything above the E threshold is F.
}

# --------------------------------------------------------------------------- #
# SWD v4 — recorded for reference only. Nothing in the codebase uses these.
# co2.js dist/esm/constants/index.js -> SWDV4, SWDMV4_RATINGS
#
# If you ever switch to v4 you must adopt BOTH blocks together, and update
# MASTERSPEC §9.2's bands in the same change.
# --------------------------------------------------------------------------- #

SWD_V4_OPERATIONAL_KWH_PER_GB: Final[dict[str, float]] = {
    "datacenter": 0.055,
    "network": 0.059,
    "device": 0.080,
}
SWD_V4_EMBODIED_KWH_PER_GB: Final[dict[str, float]] = {
    "datacenter": 0.012,
    "network": 0.013,
    "device": 0.081,
}
SWD_V4_GLOBAL_GRID_INTENSITY: Final[float] = 494.0
SWDM_V4_RATINGS: Final[dict[str, float]] = {
    "A+": 0.04,
    "A": 0.079,
    "B": 0.145,
    "C": 0.209,
    "D": 0.278,
    "E": 0.359,
}

# --------------------------------------------------------------------------- #
# Impact-panel conversions (MASTERSPEC §13, screen 5)
#
# These are NOT part of the SWD model. Sources are named individually.
# --------------------------------------------------------------------------- #

# EPA, "Greenhouse Gas Emissions from a Typical Passenger Vehicle":
# 400 g CO2e per mile => 248.5 g per km for an average passenger car.
# https://www.epa.gov/greenvehicles/greenhouse-gas-emissions-typical-passenger-vehicle
GRAMS_CO2_PER_KM_DRIVEN: Final[float] = 248.5

# EPA Greenhouse Gas Equivalencies Calculator: charging one smartphone is
# 0.00000821 metric tons CO2e = 8.21 g.
# https://www.epa.gov/energy/greenhouse-gases-equivalencies-calculator-calculations-and-references
GRAMS_CO2_PER_PHONE_CHARGE: Final[float] = 8.21

__all__ = [
    "CO2JS_SOURCE_URL",
    "CO2JS_VERSION",
    "DATACENTER_ENERGY",
    "END_USER_DEVICE_ENERGY",
    "FIRST_TIME_VIEWING_PERCENTAGE",
    "GIGABYTE",
    "GLOBAL_GRID_INTENSITY",
    "GRAMS_CO2_PER_KM_DRIVEN",
    "GRAMS_CO2_PER_PHONE_CHARGE",
    "KWH_PER_GB",
    "NETWORK_ENERGY",
    "PERCENTAGE_OF_DATA_LOADED_ON_SUBSEQUENT_LOAD",
    "PRODUCTION_ENERGY",
    "RENEWABLES_GRID_INTENSITY",
    "RETURNING_VISITOR_PERCENTAGE",
    "SWDM_V3_RATINGS",
    "SWD_MODEL_VERSION",
    "SWD_SOURCE_URL",
]
