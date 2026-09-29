"""Constants used by the trade-off detectors (MASTERSPEC §10).

Single responsibility: hold the numbers the trade-off rules need, with a
reason per value. Byte figures here are **estimates**, not measurements, and
every finding that uses one says so in its explanation.

None of these feed the carbon model or the scores. They only size the "what
does this fix cost or save in bytes" line on a trade-off card.
"""

from __future__ import annotations

from typing import Final

# --------------------------------------------------------------------------- #
# Dark mode (tension: dark_mode)
# --------------------------------------------------------------------------- #

#: Estimated transfer cost of adding a `prefers-color-scheme` block plus a
#: theme toggle. Sized from the block MASTERSPEC §8.1 fix 13 describes: a
#: `:root` custom-property set, a `@media (prefers-color-scheme: dark)`
#: override, and a small toggle script. ESTIMATE, not a measurement.
DARK_MODE_CSS_BYTES: Final[int] = 700

#: The background and text colours MASTERSPEC §13 and §8.1 fix 13 mandate for
#: dark mode. Not pure black: see the halation note in rules.json.
DARK_MODE_BACKGROUND: Final[str] = "#121212"
DARK_MODE_FOREGROUND: Final[str] = "#E8E8E8"

# --------------------------------------------------------------------------- #
# Captions (tension: captions_bytes)
# --------------------------------------------------------------------------- #

#: Estimated size of a WebVTT caption track for one short page video.
#: WebVTT is plain text: a cue is roughly a timestamp line (~30 bytes) plus a
#: line of dialogue (~50 bytes), and a clip carries roughly one cue every
#: three seconds. Two kilobytes covers a clip of about a minute with room to
#: spare. ESTIMATE, not a measurement; the real file depends on the dialogue.
WEBVTT_BYTES_PER_VIDEO: Final[int] = 2048

# --------------------------------------------------------------------------- #
# Zoom headroom (tension: high_res_zoom)
# --------------------------------------------------------------------------- #

#: An image needs enough native resolution to survive both a 2x device pixel
#: ratio and the 200% zoom WCAG 1.4.4 requires. Below this multiple of its
#: rendered width, compressing or downscaling it further will visibly blur it
#: for anyone who zooms, so the oversized_image saving must not be taken.
ZOOM_HEADROOM_FACTOR: Final[float] = 2.0

#: Images smaller than this are icons and decorations; their resolution
#: headroom is not worth a finding.
ZOOM_MIN_RENDERED_WIDTH_PX: Final[int] = 100

# --------------------------------------------------------------------------- #
# Fonts (tension: font_subsetting)
# --------------------------------------------------------------------------- #

#: Font payload above which subsetting starts to be worth discussing. Matches
#: the `font_bloat` detector threshold in MASTERSPEC §7.3 so the synergy and
#: the tension talk about the same pages.
FONT_BYTES_THRESHOLD: Final[int] = 150_000

#: Below this many font files, there is nothing to trim and no tension.
FONT_COUNT_THRESHOLD: Final[int] = 3

# --------------------------------------------------------------------------- #
# Presentation
# --------------------------------------------------------------------------- #

#: How many selectors or URLs a finding carries. The full lists stay on the
#: scan result; a card shows a sample.
MAX_EVIDENCE: Final[int] = 5

__all__ = [
    "DARK_MODE_BACKGROUND",
    "DARK_MODE_CSS_BYTES",
    "DARK_MODE_FOREGROUND",
    "FONT_BYTES_THRESHOLD",
    "FONT_COUNT_THRESHOLD",
    "MAX_EVIDENCE",
    "WEBVTT_BYTES_PER_VIDEO",
    "ZOOM_HEADROOM_FACTOR",
    "ZOOM_MIN_RENDERED_WIDTH_PX",
]
