"""axe-core mapping and ARIA snapshot tests (MASTERSPEC §6.3, §6.5).

:func:`map_axe_results` is tested against a recorded-shape axe result; the
browser tests inject the real vendored axe build into tiny fixtures served
through a Playwright route.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from app.models import Impact
from app.scanner.a11y import AXE_PATH, AXE_TAGS, MAX_HTML_SNIPPET, map_axe_results, run_axe
from app.scanner.aria import snapshot

RAW_AXE = {
    "violations": [
        {
            "id": "color-contrast",
            "impact": "serious",
            "help": "Elements must meet minimum color contrast ratio thresholds",
            "helpUrl": "https://dequeuniversity.com/rules/axe/4.13/color-contrast",
            "tags": ["cat.color", "wcag2aa", "wcag143"],
            "nodes": [
                {
                    "target": [".fine-print"],
                    "html": '<p class="fine-print">',
                    "failureSummary": "Fix: 1.9:1",
                },
                {"target": [".card__meta"], "html": '<p class="card__meta">', "failureSummary": ""},
            ],
        },
        {
            "id": "image-alt",
            "impact": "critical",
            "help": "Images must have alternative text",
            "helpUrl": "https://dequeuniversity.com/rules/axe/4.13/image-alt",
            "tags": ["wcag2a", "wcag111"],
            "nodes": [{"target": ["iframe", "img.a"], "html": "<img>" + "x" * 5000}],
        },
        {"id": "region", "impact": None, "help": "", "nodes": [{"target": ["div"]}]},
        "not-a-dict",
    ],
    "incomplete": [{"id": "video-caption"}, {"id": "color-contrast"}],
}


def test_map_axe_results_counts_and_orders():
    result = map_axe_results(RAW_AXE)

    assert [v.rule_id for v in result.violations] == ["image-alt", "color-contrast", "region"]
    assert result.unique_rules == 3
    assert result.total_nodes == 4
    assert result.counts_by_impact == {Impact.CRITICAL: 1, Impact.SERIOUS: 1, Impact.MINOR: 1}
    # incomplete is counted, never scored (MASTERSPEC §6.3).
    assert result.incomplete_count == 2


def test_map_axe_results_node_details():
    result = map_axe_results(RAW_AXE)
    contrast = next(v for v in result.violations if v.rule_id == "color-contrast")
    assert contrast.impact is Impact.SERIOUS
    assert contrast.help_url.endswith("/color-contrast")
    assert contrast.nodes[0].selector == ".fine-print"
    assert contrast.nodes[0].failure_summary == "Fix: 1.9:1"

    image = next(v for v in result.violations if v.rule_id == "image-alt")
    # Targets inside an iframe are joined into one selector path.
    assert image.nodes[0].selector == "iframe img.a"
    assert len(image.nodes[0].html) == MAX_HTML_SNIPPET + 1


def test_null_impact_is_kept_as_minor():
    region = next(v for v in map_axe_results(RAW_AXE).violations if v.rule_id == "region")
    assert region.impact is Impact.MINOR


def test_empty_axe_result():
    result = map_axe_results({})
    assert result.violations == []
    assert result.unique_rules == 0


def test_axe_tags_match_the_spec():
    assert AXE_TAGS == ("wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice")


def test_vendored_axe_matches_its_recorded_hash():
    """The vendored file must be exactly the version recorded (CLAUDE.md pinning)."""
    record = json.loads((AXE_PATH.parent / "axe-version.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256(AXE_PATH.read_bytes()).hexdigest()
    assert digest == record["sha256"]
    assert record["version"] == "4.13.0"


# --------------------------------------------------------------------------- #
# Real axe in a real browser
# --------------------------------------------------------------------------- #

BAD_HTML = """<!doctype html>
<html><head><title>Bad</title></head>
<body>
  <main>
    <h1>Title</h1>
    <img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">
    <button><span aria-hidden="true">&times;</span></button>
    <p style="color:#bbb;background:#fff">Low contrast text</p>
  </main>
</body></html>
"""

GOOD_HTML = """<!doctype html>
<html lang="en"><head><title>Good</title></head>
<body>
  <main>
    <h1>Title</h1>
    <img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" alt="A dot">
    <button>Close</button>
    <p style="color:#222;background:#fff">Readable text</p>
  </main>
</body></html>
"""


async def _with_page(html: str, executable: str | None, action):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=executable)
        try:
            page = await browser.new_page()
            await page.route(
                "**/*",
                lambda route: route.fulfill(
                    status=200, content_type="text/html; charset=utf-8", body=html
                ),
            )
            await page.goto("http://fixture.test/", wait_until="load")
            return await action(page)
        finally:
            await browser.close()


@pytest.mark.browser
async def test_run_axe_finds_planted_violations(chromium_executable: str | None):
    result = await _with_page(BAD_HTML, chromium_executable, run_axe)
    rules = {v.rule_id for v in result.violations}
    assert {"html-has-lang", "image-alt", "button-name", "color-contrast"} <= rules


@pytest.mark.browser
async def test_run_axe_clean_fixture(chromium_executable: str | None):
    result = await _with_page(GOOD_HTML, chromium_executable, run_axe)
    assert result.violations == []


@pytest.mark.browser
async def test_aria_snapshot_shows_the_accessible_tree(chromium_executable: str | None):
    text = await _with_page(GOOD_HTML, chromium_executable, snapshot)
    assert 'heading "Title"' in text
    assert 'button "Close"' in text
    assert 'img "A dot"' in text
