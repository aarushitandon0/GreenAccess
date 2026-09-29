"""Accessibility-tree snapshot (MASTERSPEC §6.5).

Playwright's ``aria_snapshot()`` renders the accessibility tree as YAML-ish
text: roughly what a screen reader exposes. The UI shows it as a tree
("Screen-reader view", MASTERSPEC §3 feature 14) so a sighted developer can see
the page the way assistive technology receives it.

Stored as raw text, exactly as returned.
"""

from __future__ import annotations

import logging

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

logger = logging.getLogger(__name__)

# A very large snapshot helps nobody and bloats every stored scan.
MAX_SNAPSHOT_CHARS = 200_000


async def snapshot(page: Page) -> str:
    """Return the ARIA snapshot of ``body``, or "" if it cannot be taken."""
    try:
        text = await page.locator("body").aria_snapshot()
    except PlaywrightError as exc:
        logger.warning("aria_snapshot failed: %s", exc)
        return ""

    if not isinstance(text, str):
        return ""
    if len(text) > MAX_SNAPSHOT_CHARS:
        return text[:MAX_SNAPSHOT_CHARS] + "\n… (truncated)"
    return text
