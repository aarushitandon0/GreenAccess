"""Our SWD port must match CO2.js itself (MASTERSPEC §7.1).

The fixture is produced by ``scripts/gen_co2_fixtures.mjs``, which runs the real
``@tgwf/co2`` package. If these fail, the Python port has drifted from the
reference implementation and the carbon numbers are wrong.

MASTERSPEC §7.1 asks for agreement within 0.1%. We assert a good deal tighter
than that where the values are non-zero, because a literal port should agree to
floating-point noise, and a 0.1% drift would mean a real difference in the
model rather than rounding.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.carbon import constants, swd

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "co2js.json"

# MASTERSPEC §7.1's requirement.
REQUIRED_RELATIVE_TOLERANCE = 1e-3
# What a literal port should actually achieve.
STRICT_RELATIVE_TOLERANCE = 1e-9


def _load() -> dict[str, Any]:
    with FIXTURE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


FIXTURE = _load()
CASES = FIXTURE["cases"]


def _case_id(case: dict[str, Any]) -> str:
    return f"{case['bytes']}B-{'green' if case['green'] else 'grey'}"


# --------------------------------------------------------------------------- #
# The fixture describes the model we think we implement
# --------------------------------------------------------------------------- #


def test_fixture_pins_co2js_version():
    assert FIXTURE["co2js_version"] == constants.CO2JS_VERSION


def test_fixture_pins_swd_model_v3():
    """The bands in MASTERSPEC §9.2 are v3 bands; the model must match."""
    assert str(FIXTURE["swd_model_version"]) == constants.SWD_MODEL_VERSION == "3"


def test_world_grid_intensity_matches_the_constant_we_copied():
    assert FIXTURE["world_grid_intensity"] == pytest.approx(
        constants.GLOBAL_GRID_INTENSITY, rel=STRICT_RELATIVE_TOLERANCE
    )


def test_fixture_covers_at_least_ten_byte_counts():
    """MASTERSPEC §7.1 asks for fixtures at 10 byte counts, green and non-green."""
    byte_counts = {case["bytes"] for case in CASES}
    assert len(byte_counts) >= 10
    assert {case["green"] for case in CASES} == {True, False}


# --------------------------------------------------------------------------- #
# Parity
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_per_byte_matches_co2js(case: dict[str, Any]):
    ours = swd.per_byte(case["bytes"], green=case["green"])
    assert ours == pytest.approx(case["per_byte"], rel=STRICT_RELATIVE_TOLERANCE, abs=1e-18)


@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_per_visit_matches_co2js(case: dict[str, Any]):
    ours = swd.per_visit(case["bytes"], green=case["green"])
    assert ours == pytest.approx(case["per_visit"], rel=STRICT_RELATIVE_TOLERANCE, abs=1e-18)


@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_per_visit_is_within_the_spec_tolerance(case: dict[str, Any]):
    """The explicit MASTERSPEC §7.1 requirement, asserted on its own terms."""
    expected = case["per_visit"]
    ours = swd.per_visit(case["bytes"], green=case["green"])
    if expected == 0:
        assert ours == 0
        return
    relative_error = abs(ours - expected) / expected
    assert relative_error < REQUIRED_RELATIVE_TOLERANCE, (
        f"{case['bytes']} bytes: ours={ours!r} co2js={expected!r} "
        f"relative error {relative_error:.2e}"
    )


@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_rating_matches_co2js(case: dict[str, Any]):
    """Our band table must agree with CO2.js's own rating for the same figure."""
    ours = swd.rating(swd.per_visit(case["bytes"], green=case["green"]))
    assert ours == case["rating"]


@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_segment_breakdown_matches_co2js(case: dict[str, Any]):
    """Each of the four components, not just the total."""
    segments = case["segments_per_byte"]
    if not isinstance(segments, dict):
        pytest.skip("fixture has no segment breakdown for this case")

    energy = swd.energy_per_byte_by_component(case["bytes"])
    ours = swd.co2_by_component(energy, green=case["green"])

    for key, expected in segments.items():
        if key == "total":
            continue
        assert key in ours, f"missing segment {key}"
        assert ours[key] == pytest.approx(expected, rel=STRICT_RELATIVE_TOLERANCE, abs=1e-18), key


@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_per_visit_segment_breakdown_matches_co2js(case: dict[str, Any]):
    """The cached-visit split, first and subsequent, per component."""
    segments = case["segments_per_visit"]
    if not isinstance(segments, dict):
        pytest.skip("fixture has no segment breakdown for this case")

    energy = swd.energy_per_visit_by_component(case["bytes"])
    ours = swd.co2_by_component(energy, green=case["green"])

    for key, expected in segments.items():
        if key == "total":
            continue
        assert key in ours, f"missing segment {key}"
        assert ours[key] == pytest.approx(expected, rel=STRICT_RELATIVE_TOLERANCE, abs=1e-18), key


# --------------------------------------------------------------------------- #
# Model properties that must hold regardless of the fixture
# --------------------------------------------------------------------------- #


def test_energy_shares_sum_to_one():
    assert sum(swd.segment_shares().values()) == pytest.approx(1.0, rel=1e-12)


def test_green_hosting_only_reduces_the_data_centre_segment():
    """A green host changes the data centre figure and nothing else."""
    energy = swd.energy_per_byte_by_component(1_000_000)
    grey = swd.co2_by_component(energy, green=False)
    green = swd.co2_by_component(energy, green=True)

    assert green["dataCenterCO2"] < grey["dataCenterCO2"]
    for key in ("consumerDeviceCO2", "networkCO2", "productionCO2"):
        assert green[key] == grey[key], f"{key} must not change with hosting"


def test_per_visit_is_less_than_per_byte():
    """Caching means an average view transfers less than a cold first load."""
    for byte_count in (1_000, 1_000_000, 10_000_000):
        assert swd.per_visit(byte_count) < swd.per_byte(byte_count)


def test_per_visit_applies_the_documented_caching_ratio():
    """per_visit / per_byte should be 0.75 + 0.25 x 0.02 = 0.755."""
    expected_ratio = (
        constants.FIRST_TIME_VIEWING_PERCENTAGE
        + constants.RETURNING_VISITOR_PERCENTAGE
        * constants.PERCENTAGE_OF_DATA_LOADED_ON_SUBSEQUENT_LOAD
    )
    ratio = swd.per_visit(1_000_000) / swd.per_byte(1_000_000)
    assert ratio == pytest.approx(expected_ratio, rel=1e-12)
    assert expected_ratio == pytest.approx(0.755, rel=1e-12)


def test_output_is_linear_in_bytes():
    assert swd.per_visit(2_000_000) == pytest.approx(2 * swd.per_visit(1_000_000), rel=1e-12)


def test_zero_and_sub_one_byte_counts_are_zero():
    assert swd.per_byte(0) == 0.0
    assert swd.per_byte(0.5) == 0.0
    assert swd.per_visit(0) == 0.0


# --------------------------------------------------------------------------- #
# Rating bands
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("grams", "expected"),
    [
        (0.0, "A+"),
        (0.095, "A+"),  # inclusive upper bound
        (0.0951, "A"),
        (0.186, "A"),
        (0.1861, "B"),
        (0.341, "B"),
        (0.3411, "C"),
        (0.493, "C"),
        (0.4931, "D"),
        (0.656, "D"),
        (0.6561, "E"),
        (0.846, "E"),
        (0.8461, "F"),
        (3.0, "F"),
    ],
)
def test_rating_band_boundaries(grams: float, expected: str):
    assert swd.rating(grams) == expected


def test_rating_bands_match_masterspec_9_2():
    """The bands are quoted in MASTERSPEC §9.2; they must not drift."""
    assert constants.SWDM_V3_RATINGS == {
        "A+": 0.095,
        "A": 0.186,
        "B": 0.341,
        "C": 0.493,
        "D": 0.656,
        "E": 0.846,
    }


def test_v4_bands_are_not_used_by_the_rating_function():
    """Guard against a silent model switch.

    A v4 figure fed into v3 bands grades far too well. This asserts the two
    tables are genuinely different, so the confusion is visible if anyone
    wires v4 in without changing the bands.
    """
    assert constants.SWDM_V4_RATINGS["A+"] < constants.SWDM_V3_RATINGS["A+"]
    # 0.5 g is a D on v3 bands but an F on v4 bands.
    assert swd.rating(0.5) == "D"
