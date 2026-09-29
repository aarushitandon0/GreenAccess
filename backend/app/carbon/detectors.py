"""Carbon detectors (MASTERSPEC §7.3).

Every detector is a pure function over :class:`~app.scanner.dom.DomFacts` and a
:class:`~app.scanner.network.NetworkSummary`. No page access, no I/O, so each
one is unit-tested against fixtures.

**Every byte figure here is an estimate**, computed from the constants below.
None of them is a measurement of a saving actually achieved, and the UI must
label them accordingly (CLAUDE.md, accuracy rules).

The table of rules is MASTERSPEC §7.3; the constants each rule uses are
gathered at the top of this module so they are visible in one place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlparse

from app.models import Detection, ImageIssue, ImageIssueKind, MediaItem, ResourceType
from app.scanner.dom import DomFacts, ImageFact
from app.scanner.network import NetworkSummary, RequestRecord

# --------------------------------------------------------------------------- #
# Constants (MASTERSPEC §7.3). All are documented estimates.
# --------------------------------------------------------------------------- #

#: Allowance for high-density displays: serving 2x the rendered width is fine.
DPR_ALLOWANCE: Final[int] = 2

#: legacy_format only applies above this size; below it, re-encoding is noise.
LEGACY_FORMAT_MIN_BYTES: Final[int] = 30 * 1024

#: Typical saving from re-encoding to WebP/AVIF. Estimates, per §7.3.
LEGACY_FORMAT_SAVING: Final[dict[str, float]] = {
    "jpeg": 0.30,
    "jpg": 0.30,
    "png": 0.50,
    "gif": 0.50,
}

#: text_in_image thresholds.
TEXT_IN_IMAGE_MIN_BYTES: Final[int] = 40 * 1024
TEXT_IN_IMAGE_MIN_ASPECT: Final[float] = 2.0
TEXT_IN_IMAGE_ALT_LENGTH: Final[int] = 25
#: What the equivalent real text would weigh instead of the image.
TEXT_IN_IMAGE_TEXT_BYTES: Final[int] = 2 * 1024

#: An animated GIF above this size is treated like autoplaying media.
ANIMATED_GIF_MIN_BYTES: Final[int] = 200 * 1024

#: font_bloat triggers above either of these.
FONT_COUNT_THRESHOLD: Final[int] = 3
FONT_BYTES_THRESHOLD: Final[int] = 150 * 1024
#: A reasonable budget for a subsetted pair of faces.
FONT_REASONABLE_BYTES: Final[int] = 60 * 1024

_IMAGE_EXTENSIONS = re.compile(r"\.(jpe?g|png|gif|webp|avif|svg)(?:$|[?#])", re.I)


def _format_of(url: str, mime: str = "") -> str:
    """Best guess at an image's format, from its MIME type or its extension."""
    mime = (mime or "").lower()
    if mime.startswith("image/"):
        subtype = mime.split("/", 1)[1].split(";")[0].strip()
        return {"jpeg": "jpeg", "jpg": "jpeg", "svg+xml": "svg"}.get(subtype, subtype)
    match = _IMAGE_EXTENSIONS.search(urlparse(url).path or url)
    if match:
        extension = match.group(1).lower()
        return "jpeg" if extension in {"jpg", "jpeg"} else extension
    return ""


def _normalise(url: str) -> str:
    """Strip the fragment so DOM and network URLs compare equal."""
    return (url or "").split("#", 1)[0]


@dataclass
class _Matched:
    """An image, paired with the network record that delivered it."""

    fact: ImageFact
    record: RequestRecord | None

    @property
    def bytes(self) -> int:
        return self.record.transfer_bytes if self.record else 0

    @property
    def format(self) -> str:
        return _format_of(self.fact.src, self.record.mime_type if self.record else "")


def _index_by_url(summary: NetworkSummary) -> dict[str, RequestRecord]:
    return {_normalise(r.url): r for r in summary.records if r.url and not r.failed}


def _match_images(dom: DomFacts, summary: NetworkSummary) -> list[_Matched]:
    index = _index_by_url(summary)
    return [_Matched(fact=fact, record=index.get(_normalise(fact.src))) for fact in dom.images]


# --------------------------------------------------------------------------- #
# Image detectors
# --------------------------------------------------------------------------- #


def oversized_image_saving(image_bytes: int, natural_w: int, rendered_w: int) -> int:
    """``bytes x (1 - (rendered x 2 / natural)^2)``, floored at 0 (MASTERSPEC §7.3).

    The ratio is squared because an image's data scales with area, not width.
    """
    if natural_w <= 0 or rendered_w <= 0 or image_bytes <= 0:
        return 0
    ratio = (rendered_w * DPR_ALLOWANCE) / natural_w
    if ratio >= 1:
        return 0
    return max(0, int(image_bytes * (1 - ratio**2)))


def detect_oversized_images(dom: DomFacts, summary: NetworkSummary) -> list[Detection]:
    """Images whose natural width far exceeds the space they render into."""
    findings: list[Detection] = []
    for matched in _match_images(dom, summary):
        fact = matched.fact
        if fact.natural_w <= 0 or fact.rendered_w <= 0:
            continue
        if fact.natural_w <= fact.rendered_w * DPR_ALLOWANCE:
            continue
        saving = oversized_image_saving(matched.bytes, fact.natural_w, fact.rendered_w)
        if saving <= 0:
            continue
        findings.append(
            Detection(
                detector="oversized_image",
                summary=(
                    f"{fact.natural_w}x{fact.natural_h} image rendered at "
                    f"{fact.rendered_w}x{fact.rendered_h}"
                ),
                estimated_saving_bytes=saving,
                evidence=[fact.selector or fact.src],
            )
        )
    return findings


def detect_legacy_formats(dom: DomFacts, summary: NetworkSummary) -> list[Detection]:
    """JPEG/PNG/GIF over 30 KB that WebP or AVIF would carry more cheaply."""
    findings: list[Detection] = []
    for matched in _match_images(dom, summary):
        image_format = matched.format
        ratio = LEGACY_FORMAT_SAVING.get(image_format)
        if ratio is None or matched.bytes < LEGACY_FORMAT_MIN_BYTES:
            continue
        findings.append(
            Detection(
                detector="legacy_format",
                summary=(
                    f"{image_format.upper()} at {matched.bytes:,} bytes; "
                    f"WebP or AVIF would typically save about {ratio:.0%} (estimate)"
                ),
                estimated_saving_bytes=int(matched.bytes * ratio),
                evidence=[matched.fact.selector or matched.fact.src],
            )
        )
    return findings


def detect_missing_dimensions(dom: DomFacts, summary: NetworkSummary) -> list[Detection]:
    """``<img>`` without width/height. Saves no bytes; causes layout shift."""
    offenders = [
        fact.selector or fact.src
        for fact in dom.images
        if not (fact.has_width_attribute and fact.has_height_attribute)
    ]
    if not offenders:
        return []
    return [
        Detection(
            detector="no_dimensions",
            summary=(
                f"{len(offenders)} image(s) with no width/height attributes, so the "
                "browser cannot reserve space and the layout shifts as they load"
            ),
            estimated_saving_bytes=0,
            evidence=offenders,
            saves_bytes=False,
        )
    ]


def detect_eager_below_fold(dom: DomFacts, summary: NetworkSummary) -> list[Detection]:
    """Below-fold images loaded eagerly. Bytes are deferred, not removed."""
    findings: list[Detection] = []
    for matched in _match_images(dom, summary):
        fact = matched.fact
        if not fact.below_fold:
            continue
        if fact.loading.lower() == "lazy":
            continue
        if matched.bytes <= 0:
            continue
        findings.append(
            Detection(
                detector="eager_below_fold",
                summary=(
                    f"image {int(fact.document_top)}px down the page loads eagerly; "
                    "deferring it moves the bytes off the critical path"
                ),
                estimated_saving_bytes=matched.bytes,
                evidence=[fact.selector or fact.src],
            )
        )
    return findings


def _looks_like_text_in_image(matched: _Matched) -> bool:
    """The §7.3 heuristic. Deliberately conservative; findings say 'suspected'."""
    fact = matched.fact
    if matched.bytes < TEXT_IN_IMAGE_MIN_BYTES:
        return False
    if matched.format not in {"jpeg", "png", "gif"}:
        return False

    width = fact.natural_w or fact.rendered_w
    height = fact.natural_h or fact.rendered_h
    if width <= 0 or height <= 0:
        return False
    if (width / height) < TEXT_IN_IMAGE_MIN_ASPECT:
        return False

    # Either a long alt that reads like transcribed copy, or no alt at all on a
    # heavy banner -- both are the shape this defect takes in the wild.
    alt = fact.alt
    if alt is not None and len(alt.strip()) > TEXT_IN_IMAGE_ALT_LENGTH:
        return True
    return not fact.has_alt_attribute


def detect_text_in_images(dom: DomFacts, summary: NetworkSummary) -> list[Detection]:
    """Banner images that appear to have their message baked into the pixels."""
    findings: list[Detection] = []
    for matched in _match_images(dom, summary):
        if not _looks_like_text_in_image(matched):
            continue
        saving = max(0, matched.bytes - TEXT_IN_IMAGE_TEXT_BYTES)
        findings.append(
            Detection(
                detector="text_in_image_suspected",
                summary=(
                    f"wide {matched.format.upper()} banner of {matched.bytes:,} bytes "
                    "appears to contain text; real text would be a fraction of the "
                    "size and readable by a screen reader (suspected, not confirmed)"
                ),
                estimated_saving_bytes=saving,
                evidence=[matched.fact.selector or matched.fact.src],
            )
        )
    return findings


def build_image_issues(dom: DomFacts, summary: NetworkSummary) -> list[ImageIssue]:
    """Per-image view for the carbon tab's image table (MASTERSPEC §13)."""
    issues: list[ImageIssue] = []
    for matched in _match_images(dom, summary):
        fact = matched.fact
        kinds: list[ImageIssueKind] = []
        saving = 0

        if fact.natural_w > fact.rendered_w * DPR_ALLOWANCE > 0:
            oversized = oversized_image_saving(matched.bytes, fact.natural_w, fact.rendered_w)
            if oversized > 0:
                kinds.append(ImageIssueKind.OVERSIZED)
                saving += oversized

        ratio = LEGACY_FORMAT_SAVING.get(matched.format)
        if ratio is not None and matched.bytes >= LEGACY_FORMAT_MIN_BYTES:
            kinds.append(ImageIssueKind.LEGACY_FORMAT)

        if not (fact.has_width_attribute and fact.has_height_attribute):
            kinds.append(ImageIssueKind.NO_DIMENSIONS)

        if fact.below_fold and fact.loading.lower() != "lazy":
            kinds.append(ImageIssueKind.EAGER_BELOW_FOLD)

        if _looks_like_text_in_image(matched):
            kinds.append(ImageIssueKind.TEXT_IN_IMAGE_SUSPECTED)

        if not kinds:
            continue

        issues.append(
            ImageIssue(
                url=fact.src,
                bytes=matched.bytes,
                natural_w=fact.natural_w,
                natural_h=fact.natural_h,
                rendered_w=fact.rendered_w,
                rendered_h=fact.rendered_h,
                format=matched.format,
                issues=kinds,
                selector=fact.selector,
                estimated_saving_bytes=saving,
            )
        )
    return issues


# --------------------------------------------------------------------------- #
# Media
# --------------------------------------------------------------------------- #


def build_media_items(dom: DomFacts, summary: NetworkSummary) -> list[MediaItem]:
    """Autoplaying video plus heavy animated GIFs (MASTERSPEC §7.3)."""
    index = _index_by_url(summary)
    items: list[MediaItem] = []

    for fact in dom.media:
        if not fact.autoplay:
            continue
        record = index.get(_normalise(fact.src))
        items.append(
            MediaItem(
                url=fact.src,
                bytes=record.transfer_bytes if record else 0,
                selector=fact.selector,
                kind="video",
                autoplay=True,
                loop=fact.loop,
                muted=fact.muted,
                has_poster=fact.has_poster,
            )
        )

    # A large GIF behaves like autoplaying video: it decodes and animates with
    # no way for the visitor to stop it.
    for matched in _match_images(dom, summary):
        if matched.format != "gif" or matched.bytes < ANIMATED_GIF_MIN_BYTES:
            continue
        items.append(
            MediaItem(
                url=matched.fact.src,
                bytes=matched.bytes,
                selector=matched.fact.selector,
                kind="animated_gif",
                autoplay=True,
                loop=True,
                muted=True,
                has_poster=False,
            )
        )

    return items


def detect_autoplay_media(dom: DomFacts, summary: NetworkSummary) -> list[Detection]:
    """Autoplaying media. Replacing it with a poster removes the bytes entirely."""
    findings: list[Detection] = []
    for item in build_media_items(dom, summary):
        if item.bytes <= 0:
            continue
        descriptor = "animated GIF" if item.kind == "animated_gif" else "autoplaying video"
        findings.append(
            Detection(
                detector="autoplay_media",
                summary=(
                    f"{descriptor} of {item.bytes:,} bytes plays without being asked; "
                    "a poster image and a play button would defer all of it"
                ),
                estimated_saving_bytes=item.bytes,
                evidence=[item.selector or item.url],
            )
        )
    return findings


# --------------------------------------------------------------------------- #
# Network-shaped detectors
# --------------------------------------------------------------------------- #


def detect_third_party_scripts(dom: DomFacts, summary: NetworkSummary) -> list[Detection]:
    """JavaScript served by another party."""
    if summary.third_party.script_bytes <= 0:
        return []
    hosts = ", ".join(summary.third_party.hosts) or "another origin"
    return [
        Detection(
            detector="third_party_scripts",
            summary=(
                f"{summary.third_party.script_bytes:,} bytes of third-party JavaScript from {hosts}"
            ),
            estimated_saving_bytes=summary.third_party.script_bytes,
            evidence=[
                record.url
                for record in summary.records
                if record.resource_type is ResourceType.JS
                and record.url
                and any(host in record.url for host in summary.third_party.hosts)
            ],
        )
    ]


def detect_font_bloat(dom: DomFacts, summary: NetworkSummary) -> list[Detection]:
    """More than 3 font files, or more than 150 KB of them (MASTERSPEC §7.3)."""
    fonts = summary.fonts
    over_count = fonts.count > FONT_COUNT_THRESHOLD
    over_bytes = fonts.bytes > FONT_BYTES_THRESHOLD
    if not (over_count or over_bytes):
        return []

    reasons = []
    if over_count:
        reasons.append(f"{fonts.count} font files (more than {FONT_COUNT_THRESHOLD})")
    if over_bytes:
        reasons.append(f"{fonts.bytes:,} bytes (more than {FONT_BYTES_THRESHOLD:,})")

    return [
        Detection(
            detector="font_bloat",
            summary=(
                f"{' and '.join(reasons)}; subsetting and dropping unused faces "
                f"would bring this near {FONT_REASONABLE_BYTES:,} bytes (estimate)"
            ),
            estimated_saving_bytes=max(0, fonts.bytes - FONT_REASONABLE_BYTES),
            evidence=list(fonts.urls),
        )
    ]


def detect_uncompressed_text(dom: DomFacts, summary: NetworkSummary) -> list[Detection]:
    """Text resources served without gzip or brotli."""
    items = summary.uncompressed_text
    if not items:
        return []
    total_saving = sum(item.estimated_saving_bytes for item in items)
    total_decoded = sum(item.decoded_bytes for item in items)
    return [
        Detection(
            detector="uncompressed_text",
            summary=(
                f"{len(items)} text resource(s) totalling {total_decoded:,} bytes are "
                "served with no compression; gzip or brotli typically removes about 70%"
            ),
            estimated_saving_bytes=total_saving,
            evidence=[item.url for item in items],
        )
    ]


def detect_no_reduced_motion(dom: DomFacts, summary: NetworkSummary) -> list[Detection]:
    """Animations with no ``prefers-reduced-motion`` escape hatch."""
    if not dom.has_animations or dom.has_reduced_motion_rule:
        return []

    note = (
        "the page animates but no stylesheet honours prefers-reduced-motion, so a "
        "visitor who asked their system for less motion still gets all of it"
    )
    if dom.unreadable_stylesheets:
        note += (
            f" ({dom.unreadable_stylesheets} cross-origin stylesheet(s) could not be "
            "read, so this may be incomplete)"
        )
    return [
        Detection(
            detector="no_reduced_motion",
            summary=note,
            estimated_saving_bytes=0,
            evidence=[],
            saves_bytes=False,
        )
    ]


def detect_div_soup_widgets(dom: DomFacts, summary: NetworkSummary) -> list[Detection]:
    """Clickable non-buttons (MASTERSPEC §10, a synergy detector)."""
    offenders = [
        div
        for div in dom.clickable_divs
        if not div.has_tabindex or div.role not in {"button", "link"}
    ]
    if not offenders:
        return []
    return [
        Detection(
            detector="div_soup_widgets",
            summary=(
                f"{len(offenders)} clickable element(s) that are not buttons and cannot "
                "be reached or activated from the keyboard"
            ),
            estimated_saving_bytes=0,
            evidence=[div.selector for div in offenders],
            saves_bytes=False,
        )
    ]


def detect_no_color_scheme(dom: DomFacts, summary: NetworkSummary) -> list[Detection]:
    """Page with no `prefers-color-scheme` handling (MASTERSPEC §10 tension).

    A page fact rather than a byte saving: the dark-mode trade-off rule fires
    only on the absence of colour-scheme handling, so a page that already
    honours the media query is left alone.
    """
    if dom.has_color_scheme_support:
        return []

    note = (
        "no prefers-color-scheme media query, color-scheme declaration or meta tag, "
        "so every visitor gets the light theme whatever their system asks for"
    )
    if dom.unreadable_stylesheets:
        note += (
            f" ({dom.unreadable_stylesheets} cross-origin stylesheet(s) could not be "
            "read, so this may be incomplete)"
        )
    return [
        Detection(
            detector="no_color_scheme",
            summary=note,
            estimated_saving_bytes=0,
            evidence=["document"],
            saves_bytes=False,
        )
    ]


def detect_video_no_captions(dom: DomFacts, summary: NetworkSummary) -> list[Detection]:
    """Video with no caption or subtitle track (MASTERSPEC §10 tension)."""
    offenders = [item for item in dom.media if item.tag == "video" and not item.has_captions]
    if not offenders:
        return []
    return [
        Detection(
            detector="video_no_captions",
            summary=(
                f"{len(offenders)} video element(s) with no track of kind captions or "
                "subtitles, so the content is unavailable to deaf and hard-of-hearing "
                "visitors"
            ),
            # Captions cost bytes rather than saving them; the trade-off engine
            # sizes that cost, so nothing is claimed as a saving here.
            estimated_saving_bytes=0,
            evidence=[item.selector or item.src for item in offenders],
            saves_bytes=False,
        )
    ]


def detect_lazy_above_fold(dom: DomFacts, summary: NetworkSummary) -> list[Detection]:
    """Images in the first viewport marked `loading="lazy"` (MASTERSPEC §10)."""
    offenders = [
        image
        for image in dom.images
        if not image.is_background
        and not image.below_fold
        and image.loading.strip().lower() == "lazy"
    ]
    if not offenders:
        return []
    return [
        Detection(
            detector="lazy_above_fold",
            summary=(
                f"{len(offenders)} image(s) inside the first viewport carry "
                "loading=lazy, which delays content the visitor is already looking at"
            ),
            # The bytes arrive either way; only their timing changes.
            estimated_saving_bytes=0,
            evidence=[image.selector or image.src for image in offenders],
            saves_bytes=False,
        )
    ]


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

#: Every detector, in the order findings are reported.
DETECTORS: Final[tuple] = (
    detect_oversized_images,
    detect_legacy_formats,
    detect_missing_dimensions,
    detect_eager_below_fold,
    detect_text_in_images,
    detect_autoplay_media,
    detect_third_party_scripts,
    detect_font_bloat,
    detect_uncompressed_text,
    detect_no_reduced_motion,
    detect_div_soup_widgets,
    detect_no_color_scheme,
    detect_video_no_captions,
    detect_lazy_above_fold,
)


def run_all(dom: DomFacts, summary: NetworkSummary) -> list[Detection]:
    """Run every detector and return the combined findings."""
    findings: list[Detection] = []
    for detector in DETECTORS:
        findings.extend(detector(dom, summary))
    return findings
