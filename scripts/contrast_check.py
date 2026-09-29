#!/usr/bin/env python3
"""Verify the GreenAccess design tokens meet WCAG 2.1 AA contrast (MASTERSPEC §13).

The token values are parsed out of ``frontend/src/styles/tokens.css`` rather than
duplicated here, so the check cannot drift from the stylesheet it guards. Every
foreground/background combination the UI actually uses is registered in PAIRS
below and asserted for both the light and the dark theme.

Thresholds (WCAG 2.1):
  * 1.4.3 Contrast (Minimum) -- 4.5:1 for normal text, 3:1 for large text
    (>=18.66px bold or >=24px regular).
  * 1.4.11 Non-text Contrast -- 3:1 for meaningful UI boundaries and graphics.

Run directly or via ``make lint``. Exits non-zero on any failure.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOKENS_CSS = REPO_ROOT / "frontend" / "src" / "styles" / "tokens.css"

# Required ratio per usage class.
NORMAL_TEXT = 4.5
LARGE_TEXT = 3.0
UI_COMPONENT = 3.0


@dataclass(frozen=True)
class Pair:
    """One foreground/background combination the UI renders."""

    fg: str
    bg: str
    required: float
    usage: str

    @property
    def label(self) -> str:
        return f"--{self.fg} on --{self.bg}"


# Every pair the UI actually paints. Adding a new colour combination to the
# product means adding it here too.
PAIRS: tuple[Pair, ...] = (
    # Body and surface text.
    Pair("ink", "bg", NORMAL_TEXT, "body text on page background"),
    Pair("ink", "surface", NORMAL_TEXT, "body text on cards"),
    Pair("ink-muted", "bg", NORMAL_TEXT, "secondary text on page background"),
    Pair("ink-muted", "surface", NORMAL_TEXT, "secondary text on cards"),
    Pair("ink", "leaf", NORMAL_TEXT, "text on the leaf tint"),
    # Brand / links / primary actions.
    Pair("forest", "bg", NORMAL_TEXT, "links and accents on page background"),
    Pair("forest", "surface", NORMAL_TEXT, "links and accents on cards"),
    Pair("forest", "leaf", NORMAL_TEXT, "accent text on the leaf tint"),
    Pair("forest-strong", "bg", NORMAL_TEXT, "emphasised accent on background"),
    Pair("forest-strong", "surface", NORMAL_TEXT, "emphasised accent on cards"),
    Pair("on-forest", "forest", NORMAL_TEXT, "label on a primary button"),
    Pair("on-forest", "forest-strong", NORMAL_TEXT, "label on a hovered button"),
    # Status colours.
    Pair("danger", "bg", NORMAL_TEXT, "error text on page background"),
    Pair("danger", "surface", NORMAL_TEXT, "error text on cards"),
    Pair("on-danger", "danger", NORMAL_TEXT, "label on a danger fill"),
    Pair("amber-text-strong", "amber-fill", NORMAL_TEXT, "normal text on an amber chip"),
    # MASTERSPEC §13 ships --amber-text at 4.02:1 on --amber-fill. It is kept
    # unchanged and is valid for large text only; --amber-text-strong above is
    # what normal-size text uses. Documented in docs/CONTRAST.md.
    Pair("amber-text", "amber-fill", LARGE_TEXT, "large text on an amber chip"),
    # Non-text contrast (WCAG 1.4.11).
    Pair("border-strong", "bg", UI_COMPONENT, "input and control outlines on background"),
    Pair("border-strong", "surface", UI_COMPONENT, "input and control outlines on cards"),
    Pair("focus-color", "bg", UI_COMPONENT, "focus ring against page background"),
    Pair("focus-color", "surface", UI_COMPONENT, "focus ring against cards"),
    Pair("focus-color", "leaf", UI_COMPONENT, "focus ring against the leaf tint"),
)

# --border is a decorative hairline. WCAG 1.4.11 exempts purely decorative
# boundaries, so it is deliberately absent from PAIRS; anything that carries
# meaning must use --border-strong instead.
DECORATIVE_ONLY = {"border"}


# --------------------------------------------------------------------------- #
# Colour maths (WCAG 2.1 relative luminance and contrast ratio)
# --------------------------------------------------------------------------- #


def parse_hex(value: str) -> tuple[int, int, int]:
    """Parse ``#rgb`` or ``#rrggbb`` into an 8-bit RGB triple."""
    raw = value.strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) != 6:
        raise ValueError(f"not a hex colour: {value!r}")
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def _channel_luminance(channel: int) -> float:
    c = channel / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG 2.1 relative luminance."""
    r, g, b = (_channel_luminance(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 2.1 contrast ratio between two hex colours, in the range 1..21."""
    lum_a = relative_luminance(parse_hex(fg))
    lum_b = relative_luminance(parse_hex(bg))
    lighter, darker = max(lum_a, lum_b), min(lum_a, lum_b)
    return (lighter + 0.05) / (darker + 0.05)


# --------------------------------------------------------------------------- #
# tokens.css parsing
# --------------------------------------------------------------------------- #

_DECL = re.compile(r"--([a-z0-9-]+)\s*:\s*([^;]+);")
_VAR_REF = re.compile(r"var\(\s*--([a-z0-9-]+)\s*\)")


def _block(css: str, selector: str) -> str:
    """Return the body of the first rule whose selector text matches."""
    start = css.find(selector)
    if start == -1:
        raise LookupError(f"selector not found in tokens.css: {selector!r}")
    open_brace = css.index("{", start)
    depth, i = 0, open_brace
    while i < len(css):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[open_brace + 1 : i]
        i += 1
    raise LookupError(f"unbalanced braces after {selector!r}")


def _declarations(block: str) -> dict[str, str]:
    return {name: value.strip() for name, value in _DECL.findall(block)}


def resolve(tokens: dict[str, str], name: str, _seen: frozenset[str] = frozenset()) -> str:
    """Resolve a token to a literal hex colour, following ``var()`` aliases."""
    if name in _seen:
        raise ValueError(f"circular token reference at --{name}")
    if name not in tokens:
        raise LookupError(f"token --{name} is not defined in tokens.css")
    value = tokens[name]
    ref = _VAR_REF.search(value)
    if ref:
        return resolve(tokens, ref.group(1), _seen | {name})
    return value


def load_themes() -> dict[str, dict[str, str]]:
    """Return {theme_name: {token: raw_value}} for the light and dark themes."""
    css = TOKENS_CSS.read_text(encoding="utf-8")

    light = _declarations(_block(css, ":root {"))
    dark_overrides = _declarations(_block(css, ":root[data-theme="))

    # The dark theme inherits every token it does not override.
    dark = {**light, **dark_overrides}

    # The prefers-color-scheme block must stay in sync with the explicit
    # [data-theme] dark block, or the two ways of getting dark mode diverge.
    media_overrides = _declarations(_block(css, ":root:not([data-theme="))
    drift = {
        key
        for key in set(media_overrides) | set(dark_overrides)
        if media_overrides.get(key) != dark_overrides.get(key)
    }
    if drift:
        raise SystemExit(
            "tokens.css: the prefers-color-scheme dark block and the "
            f"explicit dark block disagree on: {', '.join(sorted(drift))}"
        )

    return {"light": light, "dark": dark}


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def main() -> int:
    if not TOKENS_CSS.exists():
        print(f"contrast_check: tokens.css not found at {TOKENS_CSS}", file=sys.stderr)
        return 2

    themes = load_themes()
    failures: list[str] = []
    checked = 0

    for theme_name, tokens in themes.items():
        print(f"\n{theme_name.upper()} THEME")
        print(f"  {'pair':<44} {'ratio':>7}  {'need':>5}  result")
        print(f"  {'-' * 44} {'-' * 7}  {'-' * 5}  ------")
        for pair in PAIRS:
            fg_hex = resolve(tokens, pair.fg)
            bg_hex = resolve(tokens, pair.bg)
            ratio = contrast_ratio(fg_hex, bg_hex)
            ok = ratio >= pair.required
            checked += 1
            if not ok:
                failures.append(
                    f"{theme_name}: {pair.label} is {ratio:.2f}:1, "
                    f"needs {pair.required}:1 ({pair.usage})"
                )
            print(
                f"  {pair.label:<44} {ratio:>6.2f}  {pair.required:>5.1f}  "
                f"{'PASS' if ok else 'FAIL'}"
            )

    # Guard the decorative exemption: it must not creep into a text pair.
    for pair in PAIRS:
        if pair.fg in DECORATIVE_ONLY:
            failures.append(
                f"--{pair.fg} is registered decorative-only but appears as a "
                f"foreground in {pair.label}"
            )

    print(f"\n{checked} pairs checked across {len(themes)} themes.")
    if failures:
        print(f"\n{len(failures)} FAILURE(S):", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print("All token pairs meet their WCAG 2.1 AA target.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
