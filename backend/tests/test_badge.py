"""The embeddable badge: the pure renderer and the route (MASTERSPEC §12, §13)."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

import pytest

from app.api.badge import (
    _AMBER_FILL,
    _AMBER_TEXT,
    _DANGER,
    _FOREST,
    _SURFACE,
    BADGE_HEIGHT,
    BADGE_WIDTH,
    render_badge,
)
from app.models import CarbonGrade, Scores
from tests.api_support import (
    StubControl,
    make_settings,
    read_events,
    running_app,
    wait_until,
)

SCANNED_AT = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def scores(**overrides: object) -> Scores:
    values: dict[str, object] = {
        "a11y": 66,
        "carbon": 90,
        "combined": 78,
        "carbon_grade": CarbonGrade.A,
        "is_placeholder": False,
    }
    values.update(overrides)
    return Scores(**values)  # type: ignore[arg-type]


def render(**overrides: object) -> str:
    """Render with `scores()` defaults; `grams` and `host` are not score fields."""
    grams = float(overrides.pop("grams", 0.1317))  # type: ignore[arg-type]
    host = str(overrides.pop("host", "example.com"))
    return render_badge(scores(**overrides), grams_per_view=grams, host=host, scanned_at=SCANNED_AT)


# --------------------------------------------------------------------------- #
# The document itself
# --------------------------------------------------------------------------- #


def test_the_badge_is_well_formed_xml() -> None:
    root = ElementTree.fromstring(render())
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert root.get("width") == str(BADGE_WIDTH)
    assert root.get("height") == str(BADGE_HEIGHT)


def test_the_badge_is_self_contained() -> None:
    """An <img>-embedded SVG cannot fetch anything, so it must not try."""
    svg = render()
    for forbidden in ("<script", "<image", "<use", "xlink:href", "@import", "url(http"):
        assert forbidden not in svg, f"badge must not reference {forbidden}"
    assert "http" not in svg.replace('xmlns="http://www.w3.org/2000/svg"', "")


def test_the_badge_names_itself_for_assistive_technology() -> None:
    root = ElementTree.fromstring(render())
    assert root.get("role") == "img"
    title_id = root.get("aria-labelledby")
    title = root.find("{http://www.w3.org/2000/svg}title")
    assert title is not None and title.get("id") == title_id
    assert title.text is not None and "GreenAccess" in title.text


def test_every_number_on_the_badge_is_in_its_accessible_name() -> None:
    title = ElementTree.fromstring(render()).find("{http://www.w3.org/2000/svg}title")
    assert title is not None and title.text is not None
    for expected in ("78", "66", "90", "A", "0.132"):
        assert expected in title.text


# --------------------------------------------------------------------------- #
# Honesty (CLAUDE.md wording rules)
# --------------------------------------------------------------------------- #


def test_the_carbon_figure_is_labelled_an_estimate() -> None:
    assert "estimate" in render()


def test_the_badge_carries_the_scan_date_because_it_is_a_snapshot() -> None:
    assert "30 Sep 2026" in render()


def test_the_badge_never_claims_compliance() -> None:
    svg = render().lower()
    for forbidden in ("wcag compliant", "compliant", "accessible site", "certified"):
        assert forbidden not in svg


# --------------------------------------------------------------------------- #
# Presentation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("combined", "fill", "text"),
    [
        (100, _FOREST, _SURFACE),
        (70, _FOREST, _SURFACE),
        (69, _AMBER_FILL, _AMBER_TEXT),
        (40, _AMBER_FILL, _AMBER_TEXT),
        (39, _DANGER, _SURFACE),
        (0, _DANGER, _SURFACE),
    ],
)
def test_the_headline_block_bands_by_score(combined: int, fill: str, text: str) -> None:
    svg = render(combined=combined)
    assert f'fill="{fill}"' in svg
    assert f'fill="{text}"' in svg


@pytest.mark.parametrize(
    ("grams", "expected"),
    [(0.1317, "0.132"), (0.0949, "0.095"), (1.0, "1.00"), (2.5, "2.50")],
)
def test_grams_precision_does_not_imply_false_accuracy(grams: float, expected: str) -> None:
    assert expected in render(grams=grams)


@pytest.mark.parametrize("grade", list(CarbonGrade))
def test_every_grade_renders_including_a_plus(grade: CarbonGrade) -> None:
    svg = render(carbon_grade=grade)
    assert ElementTree.fromstring(svg) is not None
    assert f"grade {grade.value}" in svg.replace("&#43;", "+")


def test_a_hostile_host_cannot_inject_markup() -> None:
    """`host` comes from a stranger's URL and this document is served to others."""
    svg = render(host='a"><script>alert(1)</script>')
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg
    assert ElementTree.fromstring(svg) is not None


def test_a_long_host_cannot_overflow_the_card() -> None:
    svg = render(host="a" * 200)
    assert "a" * 200 not in svg
    assert ElementTree.fromstring(svg) is not None


def test_the_inlined_tokens_match_the_stylesheet() -> None:
    """The badge inlines the §13 tokens because an embedded SVG has no CSS.

    That duplication is the reason for this test: if the palette moves, this
    fails rather than the badge quietly drifting away from the product.
    """
    tokens = Path(__file__).resolve().parents[2] / "frontend" / "src" / "styles" / "tokens.css"
    light = tokens.read_text(encoding="utf-8").split("@media")[0]
    for name, inlined in (
        ("ink", "#1b2a22"),
        ("ink-muted", "#4a5a50"),
        ("forest", _FOREST),
        ("amber-fill", _AMBER_FILL),
        ("amber-text", _AMBER_TEXT),
        ("danger", _DANGER),
        ("border", "#ddd8cc"),
    ):
        match = re.search(rf"^\s*--{name}:\s*(#[0-9a-fA-F]{{3,8}});", light, re.MULTILINE)
        assert match is not None, f"--{name} not found in tokens.css"
        assert match.group(1).lower() == inlined.lower(), (
            f"--{name} is {match.group(1)} in tokens.css but {inlined} in badge.py"
        )


# --------------------------------------------------------------------------- #
# The route
# --------------------------------------------------------------------------- #


async def test_the_route_serves_an_svg(tmp_path: Path) -> None:
    async with running_app(make_settings(tmp_path)) as (client, _):
        created = await client.post("/api/scans", json={"url": "http://demo.test/"})
        scan_id = created.json()["scan_id"]
        await read_events(client, scan_id)  # runs the stub job to completion
        response = await client.get(f"/api/badge/{scan_id}.svg")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["cache-control"] == "public, max-age=3600"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert ElementTree.fromstring(response.text) is not None


async def test_an_unknown_scan_is_a_404(tmp_path: Path) -> None:
    async with running_app(make_settings(tmp_path)) as (client, _):
        response = await client.get("/api/badge/does-not-exist.svg")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_a_scan_with_no_scored_result_is_a_404(tmp_path: Path) -> None:
    """A running scan has nothing honest to put on a badge.

    The job is held open on the gate rather than raced: without it the stub
    finishes fast enough that this sometimes asserts against a done scan.
    """
    control = StubControl(gate=asyncio.Event())
    async with running_app(make_settings(tmp_path), control=control) as (client, _):
        created = await client.post("/api/scans", json={"url": "http://demo.test/"})
        scan_id = created.json()["scan_id"]
        await wait_until(lambda: control.started == [scan_id])
        response = await client.get(f"/api/badge/{scan_id}.svg")
        assert control.gate is not None
        control.gate.set()
    assert response.status_code == 404
