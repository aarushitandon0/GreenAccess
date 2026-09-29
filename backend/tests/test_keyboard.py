"""Tab-crawl and focus-trap tests (MASTERSPEC §6.4, §15).

Two halves:

* Pure tests for :func:`detect_trap`, which is where the cycle logic lives.
* Browser tests against two tiny HTML fixtures — one with a trap, one clean —
  served through a Playwright route so nothing touches the network.

The browser tests are marked ``browser`` and skip automatically if Chromium is
not installed, so the suite still runs on a machine without it.
"""

from __future__ import annotations

import pytest

from app.scanner.keyboard import FocusStop, crawl, detect_trap

# --------------------------------------------------------------------------- #
# Pure cycle detection
# --------------------------------------------------------------------------- #


def stops_from(keys: list[str]) -> list[FocusStop]:
    return [
        FocusStop(
            index=i,
            selector=key.split("@")[0],
            tag="BUTTON",
            key=key,
            has_visible_focus=True,
            is_body=(key == "body"),
        )
        for i, key in enumerate(keys)
    ]


def test_no_trap_on_a_straight_run():
    keys = [f"div > a:nth-of-type({i})@0,0,10,10" for i in range(12)]
    detected, container = detect_trap(stops_from(keys), total_focusable=12)
    assert detected is False
    assert container is None


def test_three_element_cycle_repeating_is_a_trap():
    cycle = [
        "div#promo > button#promo-yes@0,0,10,10",
        "div#promo > button#promo-no@0,0,10,10",
        "div#promo > a#promo-terms@0,0,10,10",
    ]
    keys = [f"a:nth-of-type({i})@0,0,5,5" for i in range(5)] + cycle * 5
    detected, container = detect_trap(stops_from(keys), total_focusable=20)
    assert detected is True
    assert container == "div#promo"


def test_a_cycle_needs_three_full_repeats():
    cycle = ["x@1", "y@2", "z@3"]
    # Only two full repeats.
    keys = ["a@0"] * 3 + cycle * 2
    detected, _ = detect_trap(stops_from(keys), total_focusable=20)
    assert detected is False

    # A third repeat tips it over.
    detected, _ = detect_trap(stops_from(["a@0"] * 3 + cycle * 3), total_focusable=20)
    assert detected is True


def test_short_page_that_cycles_is_not_a_trap():
    """A four-link page cycles forever; that is correct, not a trap."""
    cycle = ["a@1", "b@2", "c@3", "d@4"]
    keys = cycle * 5
    detected, _ = detect_trap(stops_from(keys), total_focusable=4)
    assert detected is False, "wrapping around a whole short page is not a trap"


def test_cycle_is_a_trap_when_other_elements_exist_outside_it():
    cycle = ["a@1", "b@2", "c@3", "d@4"]
    detected, _ = detect_trap(stops_from(cycle * 5), total_focusable=30)
    assert detected is True


def test_single_element_trap():
    detected, container = detect_trap(
        stops_from(["div#modal > input#only@0,0,9,9"] * 6), total_focusable=15
    )
    assert detected is True
    assert container == "div#modal > input#only"


def test_cycle_longer_than_six_is_not_reported():
    """MASTERSPEC §6.4 caps a trap cycle at 6 elements."""
    cycle = [f"e{i}@{i}" for i in range(8)]
    detected, _ = detect_trap(stops_from(cycle * 4), total_focusable=40)
    assert detected is False


def test_shortest_cycle_wins():
    """A 2-element trap must be reported as 2, not as a coincidental 4 or 6."""
    cycle = ["div#m > a@1", "div#m > b@2"]
    detected, container = detect_trap(stops_from(cycle * 8), total_focusable=20)
    assert detected is True
    assert container == "div#m"


def test_empty_and_tiny_inputs_are_safe():
    assert detect_trap([], total_focusable=0) == (False, None)
    assert detect_trap(stops_from(["a@1"]), total_focusable=5) == (False, None)


def test_body_stops_are_ignored():
    detected, _ = detect_trap(stops_from(["body", "body", "body"]), total_focusable=10)
    assert detected is False


# --------------------------------------------------------------------------- #
# Browser fixtures
# --------------------------------------------------------------------------- #

TRAP_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Trap</title>
<style>:focus { outline: 3px solid #1f5d3a; }</style></head>
<body>
  <nav>
    <a href="#a">One</a> <a href="#b">Two</a> <a href="#c">Three</a>
    <a href="#d">Four</a> <a href="#e">Five</a> <a href="#f">Six</a>
    <a href="#g">Seven</a> <a href="#h">Eight</a>
  </nav>
  <main><button id="ordinary">Ordinary button</button></main>
  <div id="promo" role="dialog" aria-label="Offer">
    <button id="promo-yes">Yes</button>
    <button id="promo-no">No</button>
    <a id="promo-terms" href="#terms">Terms</a>
  </div>
  <script>
    // Same trap as the demo site: once focus is inside #promo, Tab never
    // leaves, and there is no Escape handler.
    var trapped = ['promo-yes', 'promo-no', 'promo-terms']
      .map(function (id) { return document.getElementById(id); });
    document.addEventListener('keydown', function (event) {
      if (event.key !== 'Tab') return;
      var index = trapped.indexOf(document.activeElement);
      if (index === -1) return;
      event.preventDefault();
      var next = event.shiftKey ? index - 1 : index + 1;
      if (next < 0) next = trapped.length - 1;
      if (next >= trapped.length) next = 0;
      trapped[next].focus();
    }, true);
  </script>
</body></html>
"""

CLEAN_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Clean</title>
<style>:focus-visible { outline: 3px solid #1f5d3a; outline-offset: 2px; }
       :focus { outline: 3px solid #1f5d3a; }</style></head>
<body>
  <nav>
    <a href="#a">One</a> <a href="#b">Two</a> <a href="#c">Three</a>
  </nav>
  <main>
    <button id="b1">First</button>
    <button id="b2">Second</button>
    <label for="name">Name</label><input id="name" type="text">
  </main>
  <div id="dialog" role="dialog" aria-label="Offer">
    <button id="d1">Yes</button>
    <button id="d2">No</button>
  </div>
</body></html>
"""

# A page where focus indicators have been removed outright.
NO_FOCUS_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>No focus</title>
<style>a, button { outline: none !important; box-shadow: none !important; }</style>
</head>
<body>
  <a href="#a">One</a><a href="#b">Two</a><a href="#c">Three</a>
  <button id="x">Button</button>
</body></html>
"""


async def _crawl_html(html: str, executable: str | None):
    """Serve `html` through a Playwright route and run the crawl over it."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path=executable)
        try:
            context = await browser.new_context(viewport={"width": 1366, "height": 768})
            page = await context.new_page()
            await page.route(
                "**/*",
                lambda route: route.fulfill(
                    status=200, content_type="text/html; charset=utf-8", body=html
                ),
            )
            await page.goto("http://fixture.test/", wait_until="domcontentloaded")
            return await crawl(page)
        finally:
            await browser.close()


@pytest.mark.browser
async def test_trap_fixture_is_detected(chromium_executable: str | None):

    result = await _crawl_html(TRAP_HTML, chromium_executable)

    assert result.trap_detected is True
    # The dialog holding the cycle, not one of the trapped controls.
    assert result.trap_container == "div#promo"
    assert result.tabs_pressed > 0


@pytest.mark.browser
async def test_clean_fixture_is_not_flagged(chromium_executable: str | None):

    result = await _crawl_html(CLEAN_HTML, chromium_executable)

    assert result.trap_detected is False
    assert result.trap_container is None
    # Every control should be reachable.
    assert result.unreachable_interactive_count == 0


@pytest.mark.browser
async def test_clean_fixture_reports_visible_focus(chromium_executable: str | None):

    result = await _crawl_html(CLEAN_HTML, chromium_executable)
    assert result.focus_visible_missing_count == 0


@pytest.mark.browser
async def test_removed_focus_indicator_is_reported(chromium_executable: str | None):
    """Defect FOCUS-01, in miniature."""

    result = await _crawl_html(NO_FOCUS_HTML, chromium_executable)
    assert result.focus_visible_missing_count >= 3


@pytest.mark.browser
async def test_trap_container_without_a_dialog_role_is_the_common_ancestor(
    chromium_executable: str | None,
):
    html = TRAP_HTML.replace(' role="dialog" aria-label="Offer"', "")
    result = await _crawl_html(html, chromium_executable)
    assert result.trap_detected is True
    assert result.trap_container == "div#promo"


# --------------------------------------------------------------------------- #
# Naming the trap container
# --------------------------------------------------------------------------- #


def _stop(key: str, *, ancestors: tuple[str, ...] = (), dialog: str = "") -> FocusStop:
    return FocusStop(
        index=0,
        selector=key.split("@")[0],
        tag="BUTTON",
        key=key,
        has_visible_focus=True,
        is_body=False,
        ancestors=ancestors,
        dialog=dialog,
    )


def test_trap_container_prefers_a_shared_dialog():
    cycle = [
        _stop(
            "button#yes@1", ancestors=("body", "div#promo", "div#promo > div"), dialog="div#promo"
        ),
        _stop("a#terms@2", ancestors=("body", "div#promo", "div#promo > div"), dialog="div#promo"),
    ]
    lead = [_stop(f"a:nth-of-type({i})@0,{i}", ancestors=("body", "nav")) for i in range(5)]
    detected, container = detect_trap(lead + cycle * 3, total_focusable=20)
    assert detected is True
    assert container == "div#promo"


def test_trap_container_falls_back_to_nearest_common_ancestor():
    cycle = [
        _stop("button#yes@1", ancestors=("body", "section#w", "section#w > div:nth-of-type(1)")),
        _stop("button#no@2", ancestors=("body", "section#w", "section#w > div:nth-of-type(2)")),
    ]
    detected, container = detect_trap(cycle * 3, total_focusable=20)
    assert detected is True
    assert container == "section#w"
