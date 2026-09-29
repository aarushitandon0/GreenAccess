"""Nearest accessible text colour (MASTERSPEC §8.1 fix kind 6, ``contrast``).

Single responsibility: given a foreground and background colour, find the
colour closest to the original foreground whose WCAG 2 contrast ratio against
the background meets the target (4.5:1, or 3:1 for large text). Deterministic,
pure, no LLM.

Method: keep the foreground's hue and saturation, move only its HSL lightness.
Both directions are searched (darker and lighter); in each, a bisection finds
the smallest lightness change that reaches the target, and the direction with
the smaller change wins (darker on a tie, since most failures are pale text on
a pale background). The result is rounded to a hex colour and re-verified, so
the rounding can never push it back under the target.

Formulas: WCAG 2.2 "relative luminance" and "contrast ratio" definitions,
https://www.w3.org/TR/WCAG22/#dfn-relative-luminance (sRGB, 0.04045 knee).
"""

from __future__ import annotations

import colorsys
import re
from dataclasses import dataclass
from typing import Final

__all__ = [
    "LARGE_TEXT_RATIO",
    "NORMAL_TEXT_RATIO",
    "AxeContrastFacts",
    "ColorError",
    "ContrastFix",
    "contrast_ratio",
    "is_large_text",
    "nearest_accessible_color",
    "parse_axe_contrast_summary",
    "parse_color",
    "relative_luminance",
    "to_hex",
]

#: WCAG 2 SC 1.4.3 minimums.
NORMAL_TEXT_RATIO: Final[float] = 4.5
LARGE_TEXT_RATIO: Final[float] = 3.0

#: WCAG "large scale" text: at least 18pt, or 14pt bold. 1pt = 4/3 CSS px.
LARGE_TEXT_PX: Final[float] = 24.0
LARGE_BOLD_TEXT_PX: Final[float] = 14.0 * 4.0 / 3.0
BOLD_WEIGHT: Final[int] = 700

_BISECTION_STEPS: Final[int] = 40

RGB = tuple[int, int, int]


class ColorError(ValueError):
    """A colour string could not be parsed."""


def parse_color(value: str) -> RGB:
    """Parse ``#rgb``, ``#rrggbb``, ``rgb(r, g, b)`` or ``rgba(r, g, b, 1)``."""
    text = value.strip().lower()
    if match := re.fullmatch(r"#([0-9a-f]{3})", text):
        r, g, b = (int(ch * 2, 16) for ch in match.group(1))
        return r, g, b
    if match := re.fullmatch(r"#([0-9a-f]{6})", text):
        raw = match.group(1)
        return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
    if match := re.fullmatch(
        r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*([0-9.]+)\s*)?\)", text
    ):
        alpha = match.group(4)
        if alpha is not None and float(alpha) < 1.0:
            # A translucent colour's contrast depends on what is under it.
            raise ColorError(f"translucent colour {value!r} has no fixed contrast")
        channels = tuple(int(match.group(i)) for i in (1, 2, 3))
        if any(c > 255 for c in channels):
            raise ColorError(f"channel out of range in {value!r}")
        return channels[0], channels[1], channels[2]
    raise ColorError(f"unsupported colour {value!r}")


def to_hex(rgb: RGB) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _linear(channel: int) -> float:
    c = channel / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb: RGB) -> float:
    r, g, b = (_linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: RGB, b: RGB) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def is_large_text(font_px: float, font_weight: int) -> bool:
    """WCAG large-scale text: >= 24px, or >= 18.66px when bold."""
    if font_px >= LARGE_TEXT_PX:
        return True
    return font_weight >= BOLD_WEIGHT and font_px >= LARGE_BOLD_TEXT_PX - 1e-9


@dataclass(frozen=True)
class ContrastFix:
    """The corrected colour and the ratios before and after."""

    original: str
    background: str
    color: str
    target: float
    ratio_before: float
    ratio_after: float

    @property
    def changed(self) -> bool:
        return self.color != self.original


def _with_lightness(h: float, s: float, lightness: float) -> RGB:
    r, g, b = colorsys.hls_to_rgb(h, lightness, s)
    return (
        max(0, min(255, round(r * 255))),
        max(0, min(255, round(g * 255))),
        max(0, min(255, round(b * 255))),
    )


def _search(h: float, s: float, start: float, end: float, bg: RGB, target: float) -> RGB | None:
    """Closest lightness between `start` and `end` whose colour meets `target`."""
    if contrast_ratio(_with_lightness(h, s, end), bg) < target:
        return None
    lo, hi = start, end  # lo fails (or is the original), hi passes
    for _ in range(_BISECTION_STEPS):
        mid = (lo + hi) / 2
        if contrast_ratio(_with_lightness(h, s, mid), bg) >= target:
            hi = mid
        else:
            lo = mid
    candidate = _with_lightness(h, s, hi)
    # Rounding to 8-bit channels can land a hair under the target; step on.
    step = 1 / 255 if end > start else -1 / 255
    lightness = hi
    while contrast_ratio(candidate, bg) < target:
        lightness = min(1.0, max(0.0, lightness + step))
        candidate = _with_lightness(h, s, lightness)
        if lightness in (0.0, 1.0):
            break
    return candidate if contrast_ratio(candidate, bg) >= target else None


def nearest_accessible_color(foreground: str, background: str, *, target: float) -> ContrastFix:
    """The colour nearest to `foreground` that reaches `target` on `background`.

    Always succeeds for targets up to 4.58:1, the worst case of black or white
    on a mid grey; raises :class:`ColorError` above that if nothing qualifies.
    """
    fg = parse_color(foreground)
    bg = parse_color(background)
    before = contrast_ratio(fg, bg)
    if before >= target:
        return ContrastFix(to_hex(fg), to_hex(bg), to_hex(fg), target, before, before)

    red, green, blue = (c / 255.0 for c in fg)
    h, lightness, s = colorsys.rgb_to_hls(red, green, blue)
    options: list[tuple[float, int, RGB]] = []
    for order, end in enumerate((0.0, 1.0)):  # darker first: it wins ties
        found = _search(h, s, lightness, end, bg, target)
        if found is not None:
            found_l = colorsys.rgb_to_hls(*(c / 255.0 for c in found))[1]
            options.append((abs(found_l - lightness), order, found))
    if not options:
        raise ColorError(f"no lightness of {foreground} reaches {target}:1 on {background}")
    _, _, best = min(options)
    return ContrastFix(
        original=to_hex(fg),
        background=to_hex(bg),
        color=to_hex(best),
        target=target,
        ratio_before=before,
        ratio_after=contrast_ratio(best, bg),
    )


@dataclass(frozen=True)
class AxeContrastFacts:
    """What axe's ``color-contrast`` failure summary says about one node."""

    foreground: str
    background: str
    font_px: float
    font_weight: int
    expected_ratio: float


_AXE_SUMMARY = re.compile(
    r"foreground color:\s*(?P<fg>#[0-9a-fA-F]{3,6}|rgba?\([^)]*\))"
    r".*?background color:\s*(?P<bg>#[0-9a-fA-F]{3,6}|rgba?\([^)]*\))"
    r".*?font size:\s*[0-9.]+pt\s*\((?P<px>[0-9.]+)px\)"
    r".*?font weight:\s*(?P<weight>\w+)"
    r"(?:.*?expected contrast ratio of\s*(?P<ratio>[0-9.]+):1)?",
    re.IGNORECASE | re.DOTALL,
)


def parse_axe_contrast_summary(summary: str) -> AxeContrastFacts | None:
    """Read colours, font size and target from an axe ``color-contrast`` node.

    Returns None when axe could not determine the colours (for example text
    over an image or gradient), which is exactly when a deterministic fix is
    not safe and the node is left for manual review.
    """
    match = _AXE_SUMMARY.search(summary or "")
    if match is None:
        return None
    weight_raw = match.group("weight").lower()
    weight = BOLD_WEIGHT if weight_raw == "bold" else 400
    if weight_raw.isdigit():
        weight = int(weight_raw)
    px = float(match.group("px"))
    ratio_raw = match.group("ratio")
    expected = (
        float(ratio_raw)
        if ratio_raw
        else (LARGE_TEXT_RATIO if is_large_text(px, weight) else NORMAL_TEXT_RATIO)
    )
    return AxeContrastFacts(
        foreground=match.group("fg"),
        background=match.group("bg"),
        font_px=px,
        font_weight=weight,
        expected_ratio=expected,
    )
