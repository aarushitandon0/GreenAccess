"""Keyboard Tab crawl and focus-trap detection (MASTERSPEC §6.4).

axe-core cannot find a focus trap: a trap is a behaviour over time, not a
property of the markup. So we drive the keyboard ourselves.

The crawl
---------
Focus ``body``, then press Tab up to 60 times. After each press, read the
identity of ``document.activeElement`` — tag, a selector path, and its bounding
box — and whether it shows a visible focus indicator.

Trap definition (MASTERSPEC §6.4)
---------------------------------
A trap is the same small cycle of **at most 6 elements** repeating for **at
least 3 full cycles**, while focusable elements exist outside that cycle. Both
halves matter. A short page whose entire tab order is four links will cycle
forever too, and that is correct behaviour, not a trap — which is why the
"elements outside the cycle" condition is required.

Everything reported here must be attributed to "automated keyboard crawl" in
the UI, never to axe (CLAUDE.md, accuracy rules).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from app.models import KeyboardResult

logger = logging.getLogger(__name__)

# MASTERSPEC §6.4.
MAX_TAB_PRESSES = 60
MAX_TRAP_CYCLE_LENGTH = 6
MIN_TRAP_REPEATS = 3

# Selector for things a user can normally reach with Tab.
FOCUSABLE_SELECTOR = (
    "a[href], button, input, select, textarea, summary, "
    "iframe, object, embed, area[href], audio[controls], video[controls], "
    "[tabindex], [contenteditable=''], [contenteditable='true']"
)


@dataclass(frozen=True)
class FocusStop:
    """One element the crawl landed on."""

    index: int
    selector: str
    tag: str
    #: A stable identity used for cycle detection.
    key: str
    has_visible_focus: bool
    is_body: bool


# Collect every focusable element, so we can tell "trapped" from "that is all
# there is". Disabled, hidden and negative-tabindex elements are excluded
# because Tab genuinely does not reach them.
_COUNT_FOCUSABLE_JS = """
(selector) => {
  const candidates = Array.from(document.querySelectorAll(selector));
  const reachable = candidates.filter((el) => {
    if (el.hasAttribute('disabled')) return false;
    if (el.getAttribute('aria-hidden') === 'true') return false;
    const tabindex = el.getAttribute('tabindex');
    if (tabindex !== null && Number(tabindex) < 0) return false;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    const box = el.getBoundingClientRect();
    if (box.width === 0 && box.height === 0) return false;
    return true;
  });
  return reachable.length;
}
"""

# Identify the focused element and decide whether it shows a focus indicator.
#
# "Visible focus" is judged conservatively: we only claim an indicator is
# MISSING when the element has no outline, no ring-like box-shadow, no border
# change and no background change versus its blurred state. MASTERSPEC §6.4
# says to flag only clear absence, because a subtle custom indicator is easy to
# mistake for none at all.
_DESCRIBE_ACTIVE_JS = """
() => {
  const el = document.activeElement;
  if (!el || el === document.body || el === document.documentElement) {
    return { isBody: true, selector: 'body', tag: 'BODY', key: 'body',
             hasVisibleFocus: true };
  }

  const selectorFor = (node) => {
    const parts = [];
    let current = node;
    let depth = 0;
    while (current && current.nodeType === 1 && depth < 6) {
      let part = current.tagName.toLowerCase();
      if (current.id) { parts.unshift(part + '#' + current.id); break; }
      const parent = current.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(
          (c) => c.tagName === current.tagName);
        if (siblings.length > 1) {
          part += ':nth-of-type(' + (siblings.indexOf(current) + 1) + ')';
        }
      }
      parts.unshift(part);
      current = current.parentElement;
      depth += 1;
    }
    return parts.join(' > ');
  };

  const focusedStyle = window.getComputedStyle(el);
  const outlineWidth = parseFloat(focusedStyle.outlineWidth) || 0;
  const hasOutline =
    outlineWidth > 0 &&
    focusedStyle.outlineStyle !== 'none' &&
    focusedStyle.outlineColor !== 'transparent';
  const shadow = focusedStyle.boxShadow;
  const hasShadowRing = !!shadow && shadow !== 'none';

  // Compare against the element's unfocused rendering: blur it, re-read, and
  // restore focus. This catches indicators drawn with border or background.
  let changedWhenBlurred = false;
  try {
    const before = {
      border: focusedStyle.borderColor + focusedStyle.borderWidth,
      background: focusedStyle.backgroundColor,
    };
    el.blur();
    const blurred = window.getComputedStyle(el);
    const after = {
      border: blurred.borderColor + blurred.borderWidth,
      background: blurred.backgroundColor,
    };
    changedWhenBlurred =
      before.border !== after.border || before.background !== after.background;
    el.focus({ preventScroll: true });
  } catch (error) {
    // If blur/focus is not permitted, fall back to the style checks alone.
  }

  const box = el.getBoundingClientRect();
  const selector = selectorFor(el);

  return {
    isBody: false,
    selector: selector,
    tag: el.tagName,
    key: selector + '@' + Math.round(box.x) + ',' + Math.round(box.y) +
         ',' + Math.round(box.width) + ',' + Math.round(box.height),
    hasVisibleFocus: hasOutline || hasShadowRing || changedWhenBlurred,
  };
}
"""


def detect_trap(stops: list[FocusStop], total_focusable: int) -> tuple[bool, str | None]:
    """Decide whether the crawl is stuck in a cycle. Pure, so it is unit-tested.

    Returns ``(trap_detected, container_selector)``.
    """
    keys = [stop.key for stop in stops if not stop.is_body]
    if len(keys) < 2:
        return False, None

    # Look for the shortest repeating tail first: a 2-element trap should be
    # reported as 2 elements, not as a coincidental 6.
    for cycle_length in range(1, MAX_TRAP_CYCLE_LENGTH + 1):
        needed = cycle_length * MIN_TRAP_REPEATS
        if len(keys) < needed:
            break

        tail = keys[-needed:]
        candidate = tail[:cycle_length]
        if any(tail[i] != candidate[i % cycle_length] for i in range(needed)):
            continue

        cycle_members = set(candidate)
        # A page whose whole tab order is this cycle is not trapped; it is just
        # a short page. There must be focusable elements outside the cycle.
        if total_focusable <= len(cycle_members):
            continue

        container = _common_ancestor_selector(
            [stop.selector for stop in stops if stop.key in cycle_members]
        )
        return True, container

    return False, None


def _common_ancestor_selector(selectors: list[str]) -> str | None:
    """Longest shared selector prefix, which names the trapping container."""
    if not selectors:
        return None
    split = [s.split(" > ") for s in selectors]
    shared: list[str] = []
    for parts in zip(*split, strict=False):
        if len({*parts}) != 1:
            break
        shared.append(parts[0])
    if not shared:
        return selectors[0]
    return " > ".join(shared)


async def crawl(page: Page, *, max_presses: int = MAX_TAB_PRESSES) -> KeyboardResult:
    """Run the Tab crawl over `page`."""
    try:
        total_focusable = int(await page.evaluate(_COUNT_FOCUSABLE_JS, FOCUSABLE_SELECTOR))
    except PlaywrightError as exc:
        logger.warning("could not enumerate focusable elements: %s", exc)
        total_focusable = 0

    # Start from a known place, so the first Tab lands on the first stop.
    try:
        await page.evaluate("() => { document.body.setAttribute('tabindex','-1');"
                            " document.body.focus(); }")
    except PlaywrightError as exc:
        logger.warning("could not focus body: %s", exc)

    stops: list[FocusStop] = []
    presses = 0

    for index in range(max_presses):
        try:
            await page.keyboard.press("Tab")
            presses += 1
            described = await page.evaluate(_DESCRIBE_ACTIVE_JS)
        except PlaywrightError as exc:
            logger.warning("tab crawl stopped at press %s: %s", index + 1, exc)
            break

        if not isinstance(described, dict):
            break

        stops.append(
            FocusStop(
                index=index,
                selector=str(described.get("selector") or ""),
                tag=str(described.get("tag") or ""),
                key=str(described.get("key") or ""),
                has_visible_focus=bool(described.get("hasVisibleFocus")),
                is_body=bool(described.get("isBody")),
            )
        )

    reached = {stop.key for stop in stops if not stop.is_body}
    trap_detected, trap_container = detect_trap(stops, total_focusable)

    # Count each element once, however many times the crawl passed through it.
    missing_focus: set[str] = {
        stop.key for stop in stops if not stop.is_body and not stop.has_visible_focus
    }

    unreachable = max(0, total_focusable - len(reached))

    return KeyboardResult(
        tabs_pressed=presses,
        trap_detected=trap_detected,
        trap_container=trap_container,
        unreachable_interactive_count=unreachable,
        focus_visible_missing_count=len(missing_focus),
        reached_count=len(reached),
    )
