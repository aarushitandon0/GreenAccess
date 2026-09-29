"""axe-core accessibility audit (MASTERSPEC §6.3).

Injects the vendored axe-core build into the page and runs it with the tag set
MASTERSPEC §3/§6.3 specifies, then maps the results onto :class:`A11yResult`.

Two details the spec calls out:

* axe runs with its **defaults** for frame handling, so iframes are included.
  Restricting it to the top document would silently under-report.
* ``incomplete`` results ("needs review") are counted but never scored
  (MASTERSPEC §6.3), because they are not findings — they are questions.

The axe file is vendored rather than fetched from a CDN so that scans are
reproducible and work offline; see ``app/scanner/vendor/axe-version.json``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.models import A11yResult, Impact, Violation, ViolationNode

logger = logging.getLogger(__name__)

VENDOR_DIR = Path(__file__).parent / "vendor"
AXE_PATH = VENDOR_DIR / "axe.min.js"

# MASTERSPEC §3 feature 3 and §6.3.
AXE_TAGS = ("wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice")

AXE_RUN_TIMEOUT_MS = 60_000

# Keep stored markup small; the UI shows a snippet, not the whole subtree.
MAX_HTML_SNIPPET = 1_000


class AxeUnavailable(RuntimeError):
    """The vendored axe build is missing."""


def axe_source() -> str:
    """Read the vendored axe-core source."""
    if not AXE_PATH.exists():
        raise AxeUnavailable(
            f"vendored axe-core not found at {AXE_PATH}. "
            "Run `node scripts/vendor_axe.mjs 4.13.0`."
        )
    return AXE_PATH.read_text(encoding="utf-8")


def _impact(raw: str | None) -> Impact:
    """Map axe's impact string onto our enum, defaulting to the mildest."""
    try:
        return Impact(str(raw or "").lower())
    except ValueError:
        # axe can return null impact for some rules; treat it as minor rather
        # than dropping the violation.
        return Impact.MINOR


def _node(raw: dict[str, Any]) -> ViolationNode:
    targets = raw.get("target") or []
    selector = " ".join(str(t) for t in targets) if targets else ""
    html = str(raw.get("html") or "")
    if len(html) > MAX_HTML_SNIPPET:
        html = html[:MAX_HTML_SNIPPET] + "…"
    return ViolationNode(
        selector=selector,
        html=html,
        failure_summary=str(raw.get("failureSummary") or ""),
    )


def map_axe_results(raw: dict[str, Any]) -> A11yResult:
    """Map a raw ``axe.run`` result object onto :class:`A11yResult`.

    Kept separate from the browser call so it can be unit-tested against
    recorded axe output.
    """
    violations: list[Violation] = []
    counts: dict[Impact, int] = {}
    total_nodes = 0

    for entry in raw.get("violations") or []:
        if not isinstance(entry, dict):
            continue
        impact = _impact(entry.get("impact"))
        nodes = [_node(n) for n in (entry.get("nodes") or []) if isinstance(n, dict)]
        total_nodes += len(nodes)
        counts[impact] = counts.get(impact, 0) + 1
        violations.append(
            Violation(
                rule_id=str(entry.get("id") or ""),
                impact=impact,
                help=str(entry.get("help") or ""),
                help_url=str(entry.get("helpUrl") or ""),
                tags=[str(t) for t in (entry.get("tags") or [])],
                nodes=nodes,
            )
        )

    # Most severe first, then by how widespread.
    severity_order = {
        Impact.CRITICAL: 0,
        Impact.SERIOUS: 1,
        Impact.MODERATE: 2,
        Impact.MINOR: 3,
    }
    violations.sort(key=lambda v: (severity_order[v.impact], -v.node_count, v.rule_id))

    return A11yResult(
        violations=violations,
        counts_by_impact=counts,
        unique_rules=len(violations),
        total_nodes=total_nodes,
        incomplete_count=len(raw.get("incomplete") or []),
    )


# Run axe with its default frame handling, so iframes are included
# (MASTERSPEC §6.3). Narrowing the context here would under-report.
_AXE_RUN_JS = """
async (tags) => {
  const results = await window.axe.run({
    runOnly: { type: 'tag', values: tags },
    resultTypes: ['violations', 'incomplete'],
  });
  return {
    violations: results.violations,
    incomplete: results.incomplete,
    testEngine: results.testEngine,
  };
}
"""


async def run_axe(page: Page, *, tags: tuple[str, ...] = AXE_TAGS) -> A11yResult:
    """Inject axe-core into `page` and run it."""
    try:
        await page.add_script_tag(content=axe_source())
    except PlaywrightError as exc:
        # A page with a strict CSP can refuse an injected script.
        logger.warning("could not inject axe-core: %s", exc)
        raise

    try:
        raw = await page.evaluate(_AXE_RUN_JS, list(tags))
    except PlaywrightTimeoutError as exc:
        raise TimeoutError("axe-core run timed out") from exc

    if not isinstance(raw, dict):
        logger.warning("axe returned %s, not an object", type(raw).__name__)
        return A11yResult()

    engine = raw.get("testEngine") or {}
    if isinstance(engine, dict) and engine.get("version"):
        logger.info("axe-core %s completed", engine["version"])

    return map_axe_results(raw)
