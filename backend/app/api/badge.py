"""The embeddable result badge (MASTERSPEC §12, §13 screen 6).

Single responsibility: turn one scan's finished scores into a self-contained
SVG that a site owner can embed with a plain ``<img>`` tag.

Self-contained is the whole constraint. The badge is served cross-origin into
someone else's page, where an ``<img>`` renders SVG in a restricted mode: no
external stylesheet, no webfont, no script, no network of any kind. So the
colours are literal hex values rather than the CSS custom properties the app
uses, the type is a system font stack, and the layout uses fixed columns rather
than anything that would need text measurement.

The rendering is a pure function of the scores, so it is unit-tested directly
(CLAUDE.md: functions that present scores are pure and table-driven).

Honesty rules that apply here as everywhere (CLAUDE.md, MASTERSPEC §19):
the carbon figure is labelled an estimate, the wording is "issues detected"
rather than any claim of compliance, and the badge carries the date of the scan
it came from, because a badge is a snapshot and not a live guarantee.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final
from xml.sax.saxutils import escape

from app.models import CarbonGrade, Scores

__all__ = ["BADGE_HEIGHT", "BADGE_WIDTH", "render_badge"]

BADGE_WIDTH: Final[int] = 340
BADGE_HEIGHT: Final[int] = 120

# The design tokens of MASTERSPEC §13, inlined because an embedded SVG cannot
# reach the stylesheet that defines them. Kept in sync by a test that reads
# frontend/src/styles/tokens.css.
_SURFACE: Final[str] = "#ffffff"
_INK: Final[str] = "#1b2a22"
_INK_MUTED: Final[str] = "#4a5a50"
_FOREST: Final[str] = "#1f5d3a"
_AMBER_FILL: Final[str] = "#f2b84b"
_AMBER_TEXT: Final[str] = "#7a4e00"
_DANGER: Final[str] = "#a4262c"
_BORDER: Final[str] = "#ddd8cc"

#: System stack. A webfont cannot load inside an embedded SVG.
_FONT: Final[str] = "system-ui,-apple-system,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif"

#: Score at or above which the headline block is forest, then amber, then red.
#: Chosen to match the carbon bands' own sense of good (B and up) and poor
#: (E and below) so the badge colour never contradicts the printed grade.
_GOOD_AT: Final[int] = 70
_FAIR_AT: Final[int] = 40


def _headline_colours(score: int) -> tuple[str, str]:
    """``(fill, text)`` for the headline block, always an AA-safe pairing.

    White on forest and white on red both clear 4.5:1; white on amber does not,
    which is why amber uses the dark amber ink from the token set instead.
    """
    if score >= _GOOD_AT:
        return _FOREST, _SURFACE
    if score >= _FAIR_AT:
        return _AMBER_FILL, _AMBER_TEXT
    return _DANGER, _SURFACE


def _grams(value: float) -> str:
    """Grams per view at a precision that does not imply false accuracy."""
    if value >= 1:
        return f"{value:.2f}"
    return f"{value:.3f}"


def render_badge(
    scores: Scores,
    *,
    grams_per_view: float,
    host: str,
    scanned_at: datetime,
) -> str:
    """The badge for one finished scan, as a complete SVG document.

    Every caller-supplied string is escaped: `host` comes from a URL a stranger
    submitted, and this document is served to third-party pages.
    """
    combined = scores.combined
    fill, on_fill = _headline_colours(combined)
    grade = scores.carbon_grade
    # "A+" needs no help, but the enum's value is what should be printed.
    grade_text = grade.value if isinstance(grade, CarbonGrade) else str(grade)

    safe_host = escape(host)[:38]
    date_text = escape(scanned_at.strftime("%d %b %Y"))
    grams_text = _grams(grams_per_view)

    # Described once for assistive technology, rather than leaving a stack of
    # <text> nodes to be read out as disconnected fragments.
    label = (
        f"GreenAccess: combined score {combined} of 100 for {safe_host}. "
        f"Accessibility {scores.a11y}, carbon {scores.carbon}, carbon grade "
        f"{grade_text}, an estimated {grams_text} grams CO2e per view. "
        f"Automated checks, scanned {date_text}."
    )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{BADGE_WIDTH}" \
height="{BADGE_HEIGHT}" viewBox="0 0 {BADGE_WIDTH} {BADGE_HEIGHT}" role="img" \
aria-labelledby="ga-title">
  <title id="ga-title">{escape(label)}</title>
  <rect x="0.5" y="0.5" width="{BADGE_WIDTH - 1}" height="{BADGE_HEIGHT - 1}" rx="12"
        fill="{_SURFACE}" stroke="{_BORDER}"/>
  <path d="M0.5 12.5A12 12 0 0 1 12.5 0.5H110v119H12.5A12 12 0 0 1 0.5 107.5Z"
        fill="{fill}"/>
  <g font-family="{_FONT}" text-anchor="middle">
    <text x="55" y="58" font-size="40" font-weight="700" fill="{on_fill}">{combined}</text>
    <text x="55" y="80" font-size="11" letter-spacing="0.6" fill="{on_fill}">COMBINED</text>
    <text x="55" y="99" font-size="10" fill="{on_fill}" opacity="0.85">out of 100</text>
  </g>
  <g font-family="{_FONT}">
    <text x="128" y="28" font-size="14" font-weight="700" fill="{_INK}">GreenAccess</text>
    <text x="128" y="46" font-size="11" fill="{_INK_MUTED}">{safe_host}</text>
    <text x="128" y="70" font-size="12" fill="{_INK}">
      Accessibility <tspan font-weight="700">{scores.a11y}</tspan>
    </text>
    <text x="128" y="88" font-size="12" fill="{_INK}">
      Carbon <tspan font-weight="700">{scores.carbon}</tspan>
      <tspan fill="{_INK_MUTED}">(grade {escape(grade_text)})</tspan>
    </text>
    <text x="128" y="105" font-size="10" fill="{_INK_MUTED}">
      ~{grams_text} g CO2e/view, estimate, {date_text}
    </text>
  </g>
</svg>
"""
