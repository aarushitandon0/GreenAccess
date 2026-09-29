"""Carbon detectors (MASTERSPEC §7.3).

Single responsibility: turn a page's network summary and DOM facts into
:class:`~app.models.Detection` findings, per-image :class:`~app.models.ImageIssue`
rows and :class:`~app.models.MediaItem` rows. Every function here is pure: no
browser, no network, no clock. The browser-side facts come from
:mod:`app.scanner.dom`, the byte counts from :mod:`app.scanner.network`.

Every byte figure a detector reports is an **estimate** computed with the
formulas in MASTERSPEC §7.3 and the constants in :mod:`app.carbon.constants`.
Summaries say so in words, and ``Detection.saves_bytes`` is false for the
detectors whose value is a note rather than a saving.

Detection names
---------------
The ten §7.3 detectors use their spec names. Four further *page facts* are
recorded under the names :data:`app.tradeoffs.detectors.PAGE_FACT_DETECTIONS`
already expects (``div_soup_widgets``, ``no_color_scheme``,
``video_no_captions``, ``lazy_above_fold``). They save no bytes and are only
observations; the trade-off engine decides what they mean.

Double counting
---------------
The same image URL can appear in several ``<img>`` elements but is downloaded
once, so detection totals count each URL once. Per-image savings are not summed
across issues naively either: resizing and re-encoding compound, and
"deferred" bytes (eager_below_fold) are not removed bytes. See
:func:`_combined_image_saving`.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from urllib.parse import urlparse

from PIL import Image

from app.carbon.constants import (
    ANIMATED_GIF_MIN_BYTES,
    FONT_BLOAT_MAX_BYTES,
    FONT_BLOAT_MAX_FILES,
    FONT_BUDGET_BYTES,
    LEGACY_FORMAT_MIN_BYTES,
    LEGACY_FORMAT_SAVING_RATIO,
    OVERSIZED_DPR_ALLOWANCE,
    TEXT_IN_IMAGE_MIN_ALT_CHARS,
    TEXT_IN_IMAGE_MIN_ASPECT,
    TEXT_IN_IMAGE_MIN_BYTES,
    TEXT_IN_IMAGE_TEXT_BYTES,
)
from app.models import Detection, ImageIssue, ImageIssueKind, MediaItem, ResourceType
from app.scanner.dom import ImageFact, PageFacts, StylesheetFact, VideoFact
from app.scanner.network import NetworkSummary, RequestRecord, is_third_party

__all__ = [
    "DetectorReport",
    "analyse_image",
    "css_has_motion",
    "css_has_reduced_motion_rule",
    "detect_autoplay_media",
    "detect_div_soup_widgets",
    "detect_font_bloat",
    "detect_lazy_above_fold",
    "detect_no_color_scheme",
    "detect_no_reduced_motion",
    "detect_third_party_scripts",
    "detect_uncompressed_text",
    "detect_video_no_captions",
    "image_format",
    "is_animated_gif",
    "run_detectors",
]


@dataclass
class DetectorReport:
    """Everything the detectors produced for one page."""

    detections: list[Detection] = field(default_factory=list)
    images: list[ImageIssue] = field(default_factory=list)
    autoplay_media: list[MediaItem] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Network lookups
# --------------------------------------------------------------------------- #

_MIME_FORMATS: dict[str, str] = {
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/pjpeg": "jpeg",
    "image/png": "png",
    "image/apng": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/avif": "avif",
    "image/svg+xml": "svg",
    "image/x-icon": "ico",
    "image/vnd.microsoft.icon": "ico",
}

_EXTENSION_FORMATS: dict[str, str] = {
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".jfif": "jpeg",
    ".png": "png",
    ".gif": "gif",
    ".webp": "webp",
    ".avif": "avif",
    ".svg": "svg",
    ".ico": "ico",
}


def image_format(url: str, mime_type: str = "") -> str:
    """Image format from the response MIME type, falling back to the URL.

    Returns "" when neither says. The served MIME type wins because a URL's
    extension is only a hint (``/photo?id=3`` has none, and CDNs rewrite).
    """
    mime = (mime_type or "").split(";", 1)[0].strip().lower()
    if not mime and url.startswith("data:"):
        mime = url[5:].split(";", 1)[0].split(",", 1)[0].strip().lower()
    if mime in _MIME_FORMATS:
        return _MIME_FORMATS[mime]
    path = urlparse(url).path.lower()
    for extension, fmt in _EXTENSION_FORMATS.items():
        if path.endswith(extension):
            return fmt
    return ""


def _live_records(summary: NetworkSummary) -> list[RequestRecord]:
    return [record for record in summary.records if not record.failed and record.url]


def _bytes_by_url(summary: NetworkSummary) -> dict[str, RequestRecord]:
    """The largest successful record per URL.

    A URL fetched twice (for instance by a preload and then the element) is
    one resource; the largest transfer is the one that carried the body.
    """
    by_url: dict[str, RequestRecord] = {}
    for record in _live_records(summary):
        current = by_url.get(record.url)
        if current is None or record.transfer_bytes > current.transfer_bytes:
            by_url[record.url] = record
    return by_url


def _media_bytes(summary: NetworkSummary, url: str) -> int:
    """Every byte transferred for `url`.

    Media is often fetched as several HTTP range requests for the same URL,
    each a separate CDP request, so they are summed rather than de-duplicated.
    """
    if not url:
        return 0
    return sum(record.transfer_bytes for record in _live_records(summary) if record.url == url)


def _unique_by_url(pairs: Iterable[tuple[str, int]]) -> int:
    """Sum values, counting each URL once."""
    seen: dict[str, int] = {}
    for url, value in pairs:
        key = url or f"__anon_{len(seen)}"
        seen[key] = max(seen.get(key, 0), value)
    return sum(seen.values())


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #


def _oversized_saving(bytes_: int, natural_w: int, rendered_w: int) -> int | None:
    """§7.3 oversized_image. ``None`` when the image is not oversized.

    Flag when natural width exceeds rendered width by more than the DPR 2
    allowance; the saving is ``bytes x (1 - (rendered*2 / natural)^2)``,
    floored at 0.
    """
    if rendered_w <= 0 or natural_w <= 0:
        return None
    target_w = rendered_w * OVERSIZED_DPR_ALLOWANCE
    if natural_w <= target_w:
        return None
    return max(0, int(bytes_ * (1 - (target_w / natural_w) ** 2)))


def _is_text_in_image_suspect(fact: ImageFact, bytes_: int, fmt: str) -> bool:
    """§7.3 text_in_image_suspected, without OCR or an LLM.

    A heavy (>= 40 KB), banner-shaped (>= 3:1) JPEG or PNG whose alt is either
    long (> 25 characters, suggesting it transcribes baked-in text) or missing
    entirely. A missing alt is included because a text-bearing banner with no
    alt is the worse case, and the finding is explicitly only "suspected".
    ``alt=""`` marks the image decorative and is not a suspect.
    """
    if bytes_ < TEXT_IN_IMAGE_MIN_BYTES or fmt not in {"jpeg", "png"}:
        return False
    if fact.natural_h <= 0 or fact.natural_w / fact.natural_h < TEXT_IN_IMAGE_MIN_ASPECT:
        return False
    if fact.alt is None:
        return True
    return len(fact.alt.strip()) > TEXT_IN_IMAGE_MIN_ALT_CHARS


def _is_rendered(fact: ImageFact) -> bool:
    return fact.rendered_w > 0 and fact.rendered_h > 0


@dataclass(frozen=True)
class _ImageSavings:
    """Estimated bytes per issue for one image. Absent key = issue not present."""

    oversized: int | None = None
    legacy_format: int | None = None
    text_in_image: int | None = None
    deferred: int | None = None


def _combined_image_saving(bytes_: int, savings: _ImageSavings, fmt: str) -> int:
    """Estimated bytes removed if every issue on one image were fixed.

    Resizing and re-encoding compound: the format saving applies to what is
    left after the resize. Replacing a text banner with live text removes the
    image outright, so it wins if larger. Lazy-loading defers bytes rather than
    removing them and is deliberately excluded.
    """
    remaining = float(bytes_)
    if savings.oversized is not None:
        remaining -= savings.oversized
    if savings.legacy_format is not None:
        remaining *= 1 - LEGACY_FORMAT_SAVING_RATIO.get(fmt, 0.0)
    removal = max(0, int(bytes_ - remaining))
    if savings.text_in_image is not None:
        removal = max(removal, savings.text_in_image)
    return removal


def analyse_image(
    fact: ImageFact,
    *,
    bytes_: int,
    fmt: str,
    viewport_h: int,
) -> tuple[ImageIssue, _ImageSavings]:
    """Run every image detector over one ``<img>``."""
    issues: list[ImageIssueKind] = []

    oversized = _oversized_saving(bytes_, fact.natural_w, fact.rendered_w)
    if oversized is not None:
        issues.append(ImageIssueKind.OVERSIZED)

    legacy: int | None = None
    if fmt in {"jpeg", "png", "gif"} and bytes_ > LEGACY_FORMAT_MIN_BYTES:
        issues.append(ImageIssueKind.LEGACY_FORMAT)
        legacy = int(bytes_ * LEGACY_FORMAT_SAVING_RATIO.get(fmt, 0.0))

    if not (fact.has_width_attr and fact.has_height_attr):
        issues.append(ImageIssueKind.NO_DIMENSIONS)

    deferred: int | None = None
    if _is_rendered(fact) and fact.loading != "lazy" and fact.doc_top >= viewport_h > 0:
        issues.append(ImageIssueKind.EAGER_BELOW_FOLD)
        deferred = bytes_

    text: int | None = None
    if _is_text_in_image_suspect(fact, bytes_, fmt):
        issues.append(ImageIssueKind.TEXT_IN_IMAGE_SUSPECTED)
        text = max(0, bytes_ - TEXT_IN_IMAGE_TEXT_BYTES)

    savings = _ImageSavings(
        oversized=oversized, legacy_format=legacy, text_in_image=text, deferred=deferred
    )
    issue = ImageIssue(
        url=fact.src,
        bytes=bytes_,
        natural_w=fact.natural_w,
        natural_h=fact.natural_h,
        rendered_w=fact.rendered_w,
        rendered_h=fact.rendered_h,
        format=fmt,
        issues=issues,
        selector=fact.selector,
        estimated_saving_bytes=_combined_image_saving(bytes_, savings, fmt),
    )
    return issue, savings


def _image_detections(
    analysed: list[tuple[ImageIssue, _ImageSavings]],
) -> list[Detection]:
    """Roll per-image results up into one Detection per §7.3 image detector."""

    def pick(kind: ImageIssueKind) -> list[tuple[ImageIssue, _ImageSavings]]:
        return [(issue, s) for issue, s in analysed if kind in issue.issues]

    detections: list[Detection] = []

    oversized = pick(ImageIssueKind.OVERSIZED)
    if oversized:
        saving = _unique_by_url((i.url, s.oversized or 0) for i, s in oversized)
        detections.append(
            Detection(
                detector="oversized_image",
                summary=(
                    f"{len(oversized)} image(s) are more than {OVERSIZED_DPR_ALLOWANCE:g}x "
                    f"wider than they render. Resizing to {OVERSIZED_DPR_ALLOWANCE:g}x "
                    f"rendered width would save an estimated {saving:,} bytes."
                ),
                estimated_saving_bytes=saving,
                evidence=[i.selector or i.url for i, _ in oversized],
            )
        )

    legacy = pick(ImageIssueKind.LEGACY_FORMAT)
    if legacy:
        saving = _unique_by_url((i.url, s.legacy_format or 0) for i, s in legacy)
        detections.append(
            Detection(
                detector="legacy_format",
                summary=(
                    f"{len(legacy)} JPEG/PNG/GIF image(s) over "
                    f"{LEGACY_FORMAT_MIN_BYTES // 1000} KB could be served as WebP or "
                    f"AVIF. Estimated saving {saving:,} bytes (30% of JPEG, 50% of PNG "
                    "bytes; no estimate for GIF)."
                ),
                estimated_saving_bytes=saving,
                evidence=[i.selector or i.url for i, _ in legacy],
            )
        )

    no_dims = pick(ImageIssueKind.NO_DIMENSIONS)
    if no_dims:
        detections.append(
            Detection(
                detector="no_dimensions",
                summary=(
                    f"{len(no_dims)} <img> element(s) have no width/height attributes, "
                    "so the browser cannot reserve space and the layout shifts as "
                    "they load. No byte saving; an accessibility and stability note."
                ),
                estimated_saving_bytes=0,
                evidence=[i.selector or i.url for i, _ in no_dims],
                saves_bytes=False,
            )
        )

    eager = pick(ImageIssueKind.EAGER_BELOW_FOLD)
    if eager:
        deferred = _unique_by_url((i.url, s.deferred or 0) for i, s in eager)
        detections.append(
            Detection(
                detector="eager_below_fold",
                summary=(
                    f"{len(eager)} image(s) below the first viewport load eagerly. "
                    f'loading="lazy" would defer an estimated {deferred:,} bytes until '
                    "they are scrolled to (deferred, not removed)."
                ),
                estimated_saving_bytes=deferred,
                evidence=[i.selector or i.url for i, _ in eager],
            )
        )

    text = pick(ImageIssueKind.TEXT_IN_IMAGE_SUSPECTED)
    if text:
        saving = _unique_by_url((i.url, s.text_in_image or 0) for i, s in text)
        detections.append(
            Detection(
                detector="text_in_image_suspected",
                summary=(
                    f"{len(text)} banner-shaped image(s) are suspected of carrying text "
                    "in their pixels (heuristic, not OCR; review before acting). "
                    f"Replacing them with live text would save an estimated {saving:,} bytes."
                ),
                estimated_saving_bytes=saving,
                evidence=[i.selector or i.url for i, _ in text],
            )
        )

    return detections


# --------------------------------------------------------------------------- #
# Media
# --------------------------------------------------------------------------- #


def is_animated_gif(body: bytes) -> bool:
    """True when `body` is a GIF with more than one frame.

    Uses Pillow's frame index rather than a byte scan, so every valid GIF
    layout is handled. Anything unreadable is "not animated".
    """
    if not body.startswith((b"GIF87a", b"GIF89a")):
        return False
    try:
        with Image.open(io.BytesIO(body)) as image:
            return bool(getattr(image, "is_animated", False))
    except (OSError, ValueError, Image.DecompressionBombError):
        return False


def detect_autoplay_media(
    summary: NetworkSummary,
    facts: PageFacts,
    animated_gif_urls: frozenset[str] = frozenset(),
) -> tuple[Detection | None, list[MediaItem]]:
    """§7.3 autoplay_media: autoplaying video, and animated GIFs over 200 KB.

    Saving is the full media bytes, i.e. what a poster image would replace.
    `animated_gif_urls` comes from the pipeline, which checks the bodies of
    heavy GIFs with :func:`is_animated_gif`.
    """
    items: list[MediaItem] = []

    for video in facts.videos:
        if not video.autoplay:
            continue
        items.append(
            MediaItem(
                url=video.src,
                bytes=_media_bytes(summary, video.src),
                selector=video.selector,
                kind="video",
                autoplay=True,
                loop=video.loop,
                muted=video.muted,
                has_poster=video.has_poster,
            )
        )

    selectors = {image.src: image.selector for image in facts.images}
    records = _bytes_by_url(summary)
    for url in sorted(animated_gif_urls):
        record = records.get(url)
        if record is None or record.transfer_bytes <= ANIMATED_GIF_MIN_BYTES:
            continue
        items.append(
            MediaItem(
                url=url,
                bytes=record.transfer_bytes,
                selector=selectors.get(url, ""),
                kind="animated_gif",
                autoplay=True,
                loop=True,
            )
        )

    if not items:
        return None, []

    saving = _unique_by_url((item.url, item.bytes) for item in items)
    videos = sum(1 for item in items if item.kind == "video")
    gifs = len(items) - videos
    parts = []
    if videos:
        parts.append(f"{videos} autoplaying video(s)")
    if gifs:
        parts.append(f"{gifs} animated GIF(s) over {ANIMATED_GIF_MIN_BYTES // 1000} KB")
    detection = Detection(
        detector="autoplay_media",
        summary=(
            f"{' and '.join(parts)} download on page load. Replacing them with a "
            f"poster until the user asks to play saves an estimated {saving:,} bytes."
        ),
        estimated_saving_bytes=saving,
        evidence=[item.selector or item.url for item in items],
    )
    return detection, items


# --------------------------------------------------------------------------- #
# Network-only detectors
# --------------------------------------------------------------------------- #


def detect_third_party_scripts(summary: NetworkSummary, page_url: str) -> Detection | None:
    """§7.3 third_party_scripts: JS served by another registrable domain."""
    scripts = [
        record
        for record in _live_records(summary)
        if record.resource_type is ResourceType.JS and is_third_party(record.url, page_url)
    ]
    if not scripts:
        return None
    saving = sum(record.transfer_bytes for record in scripts)
    hosts = sorted({urlparse(record.url).netloc for record in scripts})
    return Detection(
        detector="third_party_scripts",
        summary=(
            f"{len(scripts)} script(s) from {len(hosts)} third-party host(s) "
            f"({', '.join(hosts)}), {saving:,} bytes. Removing or deferring them "
            "until needed would save an estimated equal amount."
        ),
        estimated_saving_bytes=saving,
        evidence=[record.url for record in scripts],
    )


def detect_font_bloat(summary: NetworkSummary) -> Detection | None:
    """§7.3 font_bloat: more than 3 font files, or more than 150 KB of fonts."""
    fonts = summary.fonts
    if fonts.count <= FONT_BLOAT_MAX_FILES and fonts.bytes <= FONT_BLOAT_MAX_BYTES:
        return None
    saving = max(0, fonts.bytes - FONT_BUDGET_BYTES)
    return Detection(
        detector="font_bloat",
        summary=(
            f"{fonts.count} font file(s), {fonts.bytes:,} bytes. Subsetting and "
            f"dropping unused files down to a ~{FONT_BUDGET_BYTES // 1000} KB budget "
            f"would save an estimated {saving:,} bytes."
        ),
        estimated_saving_bytes=saving,
        evidence=list(fonts.urls),
    )


def detect_uncompressed_text(summary: NetworkSummary) -> Detection | None:
    """§7.3 uncompressed_text: html/css/js > 2 KB sent without gzip/br.

    The per-resource check and its 70% estimate live in the network collector,
    which already has the headers; this rolls them up.
    """
    resources = summary.uncompressed_text
    if not resources:
        return None
    saving = sum(resource.estimated_saving_bytes for resource in resources)
    decoded = sum(resource.decoded_bytes for resource in resources)
    return Detection(
        detector="uncompressed_text",
        summary=(
            f"{len(resources)} text resource(s) ({decoded:,} bytes decoded) are served "
            "without gzip or brotli. Compression would save an estimated "
            f"{saving:,} bytes (~70% of decoded size)."
        ),
        estimated_saving_bytes=saving,
        evidence=[resource.url for resource in resources],
    )


# --------------------------------------------------------------------------- #
# CSS
# --------------------------------------------------------------------------- #

_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_MOTION_DECLARATION = re.compile(
    r"(?<![\w-])(animation|animation-name|transition)\s*:\s*([^;{}]+)", re.IGNORECASE
)
_TIME_TOKEN = re.compile(r"(?<![\w.-])(\d*\.?\d+)(ms|s)\b", re.IGNORECASE)
_NONE_VALUES = frozenset({"none", "initial", "inherit", "unset", "revert", "revert-layer"})
_REDUCED_MOTION_MEDIA = re.compile(r"@media[^{]*prefers-reduced-motion", re.IGNORECASE)
_COLOR_SCHEME_MEDIA = re.compile(r"@media[^{]*prefers-color-scheme", re.IGNORECASE)
_COLOR_SCHEME_DECLARATION = re.compile(r"(?<![\w-])color-scheme\s*:\s*([^;{}]+)", re.IGNORECASE)


def _strip_comments(css: str) -> str:
    return _CSS_COMMENT.sub("", css)


def css_has_motion(css: str) -> bool:
    """True when `css` declares an animation or a non-zero transition.

    ``@keyframes`` on its own is not motion (nothing may use it); a declaration
    is. A transition whose every time value is zero moves nothing.
    """
    for match in _MOTION_DECLARATION.finditer(_strip_comments(css)):
        prop = match.group(1).lower()
        value = match.group(2).strip().lower().removesuffix("!important").strip()
        if not value or value in _NONE_VALUES:
            continue
        if prop == "transition":
            times = [float(number) for number, _unit in _TIME_TOKEN.findall(value)]
            if not times or all(t == 0 for t in times):
                continue
        return True
    return False


def css_has_reduced_motion_rule(css: str) -> bool:
    """True when `css` contains an ``@media (prefers-reduced-motion ...)`` rule."""
    return bool(_REDUCED_MOTION_MEDIA.search(_strip_comments(css)))


def _css_has_color_scheme(css: str) -> bool:
    text = _strip_comments(css)
    if _COLOR_SCHEME_MEDIA.search(text):
        return True
    for match in _COLOR_SCHEME_DECLARATION.finditer(text):
        if match.group(1).strip().lower() not in {"normal", *_NONE_VALUES}:
            return True
    return False


def detect_no_reduced_motion(stylesheets: list[StylesheetFact]) -> Detection | None:
    """§7.3 no_reduced_motion.

    Fires when any readable stylesheet animates or transitions something and
    no same-origin stylesheet has a ``prefers-reduced-motion`` media rule.
    """
    moving = [sheet for sheet in stylesheets if css_has_motion(sheet.css_text)]
    if not moving:
        return None
    if any(
        sheet.same_origin and css_has_reduced_motion_rule(sheet.css_text) for sheet in stylesheets
    ):
        return None
    return Detection(
        detector="no_reduced_motion",
        summary=(
            "CSS animations or transitions run, and no same-origin stylesheet has a "
            "@media (prefers-reduced-motion) rule. No byte saving; honouring the "
            "preference helps people with vestibular disorders and saves device CPU."
        ),
        estimated_saving_bytes=0,
        evidence=[sheet.href or "inline <style>" for sheet in moving],
        saves_bytes=False,
    )


# --------------------------------------------------------------------------- #
# Page facts for the trade-off engine (no bytes)
# --------------------------------------------------------------------------- #


def detect_div_soup_widgets(facts: PageFacts) -> Detection | None:
    """Clickable non-buttons: ``onclick`` or ``role=button`` on a non-interactive tag."""
    if not facts.clickable_non_buttons:
        return None
    count = len(facts.clickable_non_buttons)
    return Detection(
        detector="div_soup_widgets",
        summary=(
            f"{count} element(s) act as buttons (onclick or role=button) but are not "
            "<button> or <a>, so keyboard and assistive-technology users may not be "
            "able to use them."
        ),
        estimated_saving_bytes=0,
        evidence=list(facts.clickable_non_buttons),
        saves_bytes=False,
    )


def detect_no_color_scheme(facts: PageFacts) -> Detection | None:
    """No ``prefers-color-scheme`` rule, no ``color-scheme`` declaration or meta."""
    meta = facts.meta_color_scheme.strip().lower()
    if meta and meta != "normal":
        return None
    if any(_css_has_color_scheme(sheet.css_text) for sheet in facts.stylesheets):
        return None
    return Detection(
        detector="no_color_scheme",
        summary="The page offers no dark colour scheme (no prefers-color-scheme handling).",
        estimated_saving_bytes=0,
        evidence=["document"],
        saves_bytes=False,
    )


def detect_video_no_captions(videos: list[VideoFact]) -> Detection | None:
    """Videos without a ``<track kind="captions|subtitles">``."""
    missing = [video for video in videos if not video.has_captions]
    if not missing:
        return None
    return Detection(
        detector="video_no_captions",
        summary=f"{len(missing)} video(s) have no captions or subtitles track.",
        estimated_saving_bytes=0,
        evidence=[video.selector for video in missing],
        saves_bytes=False,
    )


def detect_lazy_above_fold(facts: PageFacts) -> Detection | None:
    """Images inside the first viewport that carry ``loading="lazy"``."""
    lazy = [
        image
        for image in facts.images
        if image.loading == "lazy" and _is_rendered(image) and image.doc_top < facts.viewport_h
    ]
    if not lazy:
        return None
    return Detection(
        detector="lazy_above_fold",
        summary=(
            f"{len(lazy)} image(s) in the first viewport are lazy-loaded, which "
            "delays content the visitor is already looking at."
        ),
        estimated_saving_bytes=0,
        evidence=[image.selector or image.src for image in lazy],
        saves_bytes=False,
    )


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def run_detectors(
    summary: NetworkSummary,
    facts: PageFacts,
    *,
    page_url: str,
    animated_gif_urls: frozenset[str] = frozenset(),
) -> DetectorReport:
    """Run every detector. `page_url` decides what counts as third party."""
    records = _bytes_by_url(summary)

    analysed: list[tuple[ImageIssue, _ImageSavings]] = []
    for fact in facts.images:
        record = records.get(fact.src)
        bytes_ = record.transfer_bytes if record is not None else 0
        fmt = image_format(fact.src, record.mime_type if record is not None else "")
        analysed.append(analyse_image(fact, bytes_=bytes_, fmt=fmt, viewport_h=facts.viewport_h))

    report = DetectorReport()
    report.images = [issue for issue, _ in analysed if issue.issues]
    report.detections.extend(_image_detections(analysed))

    autoplay, media = detect_autoplay_media(summary, facts, animated_gif_urls)
    report.autoplay_media = media

    for detection in (
        autoplay,
        detect_third_party_scripts(summary, page_url),
        detect_font_bloat(summary),
        detect_uncompressed_text(summary),
        detect_no_reduced_motion(facts.stylesheets),
        detect_div_soup_widgets(facts),
        detect_no_color_scheme(facts),
        detect_video_no_captions(facts.videos),
        detect_lazy_above_fold(facts),
    ):
        if detection is not None:
            report.detections.append(detection)

    return report
