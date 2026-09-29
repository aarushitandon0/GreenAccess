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

# --------------------------------------------------------------------------- #
# Detector thresholds and saving estimates (MASTERSPEC §7.3)
#
# Source: MASTERSPEC §7.3, the detector table. These are NOT part of the SWD
# model and not from CO2.js; they are the product's own documented heuristics.
# Every "saving" they produce is an ESTIMATE and is labelled as one in the
# Detection summary.
#
# Units: "KB" in §7.3 is read as decimal (1 KB = 1,000 bytes), matching the
# decimal GIGABYTE the SWD model uses. The one exception is the 2 KB
# uncompressed-text floor, which the network collector has applied as 2,048
# bytes since it was written; the 48-byte difference cannot change a finding
# that matters.
# --------------------------------------------------------------------------- #

#: oversized_image: flag when natural width > this x rendered width, and size
#: the saving as if resized to this x rendered width (the "DPR 2 allowance").
OVERSIZED_DPR_ALLOWANCE: Final[float] = 2.0

#: legacy_format: JPEG/PNG/GIF above this size, where WebP/AVIF would apply.
LEGACY_FORMAT_MIN_BYTES: Final[int] = 30_000
#: legacy_format saving share by format. §7.3 gives JPEG and PNG only; a GIF is
#: flagged but carries no byte estimate rather than an invented one.
LEGACY_FORMAT_SAVING_RATIO: Final[dict[str, float]] = {"jpeg": 0.30, "png": 0.50}

#: text_in_image_suspected: minimum image size.
TEXT_IN_IMAGE_MIN_BYTES: Final[int] = 40_000
#: text_in_image_suspected: "banner aspect ratio". §7.3 does not give a number;
#: 3:1 (width:height) is our choice, e.g. 1600x400, 728x90, 970x250.
TEXT_IN_IMAGE_MIN_ASPECT: Final[float] = 3.0
#: text_in_image_suspected: alt longer than this counts as "lots of text".
TEXT_IN_IMAGE_MIN_ALT_CHARS: Final[int] = 25
#: text_in_image_suspected: the replacement live text is "~2 KB".
TEXT_IN_IMAGE_TEXT_BYTES: Final[int] = 2_000

#: autoplay_media: an animated GIF counts above this size.
ANIMATED_GIF_MIN_BYTES: Final[int] = 200_000

#: font_bloat: flag above this many font files, OR above this many bytes.
FONT_BLOAT_MAX_FILES: Final[int] = 3
FONT_BLOAT_MAX_BYTES: Final[int] = 150_000
#: font_bloat saving: "fonts - 60 KB", i.e. a lean font budget of 60 KB.
FONT_BUDGET_BYTES: Final[int] = 60_000

#: uncompressed_text: html/css/js with no gzip/br and decoded size above this.
UNCOMPRESSED_MIN_BYTES: Final[int] = 2_048
#: uncompressed_text saving: "~70% of decoded size".
COMPRESSION_SAVING_RATIO: Final[float] = 0.70

__all__ = [
    "ANIMATED_GIF_MIN_BYTES",
    "CO2JS_SOURCE_URL",
    "CO2JS_VERSION",
    "COMPRESSION_SAVING_RATIO",
    "DATACENTER_ENERGY",
    "END_USER_DEVICE_ENERGY",
    "FIRST_TIME_VIEWING_PERCENTAGE",
    "FONT_BLOAT_MAX_BYTES",
    "FONT_BLOAT_MAX_FILES",
    "FONT_BUDGET_BYTES",
    "GIGABYTE",
    "GLOBAL_GRID_INTENSITY",
    "GRAMS_CO2_PER_KM_DRIVEN",
    "GRAMS_CO2_PER_PHONE_CHARGE",
    "KWH_PER_GB",
    "LEGACY_FORMAT_MIN_BYTES",
    "LEGACY_FORMAT_SAVING_RATIO",
    "NETWORK_ENERGY",
    "OVERSIZED_DPR_ALLOWANCE",
    "PERCENTAGE_OF_DATA_LOADED_ON_SUBSEQUENT_LOAD",
    "PRODUCTION_ENERGY",
    "RENEWABLES_GRID_INTENSITY",
    "RETURNING_VISITOR_PERCENTAGE",
    "SWDM_V3_RATINGS",
    "SWD_MODEL_VERSION",
    "SWD_SOURCE_URL",
    "TEXT_IN_IMAGE_MIN_ALT_CHARS",
    "TEXT_IN_IMAGE_MIN_ASPECT",
    "TEXT_IN_IMAGE_MIN_BYTES",
    "TEXT_IN_IMAGE_TEXT_BYTES",
    "UNCOMPRESSED_MIN_BYTES",
]
