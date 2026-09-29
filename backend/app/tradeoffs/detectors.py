"""Trade-off detector registry (MASTERSPEC §10).

Single responsibility: answer "does this rule apply to this scan?" by reading a
:class:`~app.models.ScanResult` and nothing else. No page loads, no network, no
LLM. Every function here is pure and deterministic.

Each detector returns a :class:`DetectorHit` (evidence, a byte delta, and the
fill-ins its rule's template needs) or ``None`` when the rule does not apply.
:mod:`app.tradeoffs.engine` turns hits into ``TradeoffFinding`` objects.

Sign convention for ``bytes_delta``
-----------------------------------
It is the change in transfer bytes from applying the rule's **recommended
fix**: positive when the fix saves bytes, negative when it costs them, zero
when the fix is byte-neutral. Synergies are normally positive, tensions
negative or zero. Nothing here is a measurement; the figures come from the
estimate formulas in MASTERSPEC §7.3 and :mod:`app.tradeoffs.constants`.

What the scanner must provide
-----------------------------
Detectors reuse scanner output, so some of them need a named entry in
``CarbonResult.detections``. :data:`REQUIRED_DETECTIONS` lists every name this
module looks for, split into the byte detectors MASTERSPEC §7.3 already
specifies and the page facts the trade-off rules additionally need. A rule
whose detection is absent simply does not fire: a missing signal must never
turn into an invented finding.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from app.models import Detection, ImageIssue, ImageIssueKind, ScanResult
from app.tradeoffs.constants import (
    DARK_MODE_CSS_BYTES,
    FONT_BYTES_THRESHOLD,
    FONT_COUNT_THRESHOLD,
    MAX_EVIDENCE,
    WEBVTT_BYTES_PER_VIDEO,
    ZOOM_HEADROOM_FACTOR,
    ZOOM_MIN_RENDERED_WIDTH_PX,
)

__all__ = [
    "DETECTOR_FACTS",
    "PAGE_FACT_DETECTIONS",
    "REGISTRY",
    "REQUIRED_DETECTIONS",
    "DetectorHit",
    "human_bytes",
]


class DetectorHit(BaseModel):
    """What a detector found. Consumed only by :mod:`app.tradeoffs.engine`."""

    model_config = ConfigDict(extra="forbid")

    #: Selectors or URLs that evidence the finding, already truncated.
    evidence: list[str] = Field(default_factory=list)
    #: Transfer-byte change from the recommended fix. See the sign convention.
    bytes_delta: int = 0
    #: Template fill-ins, pre-formatted as strings.
    facts: dict[str, str] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# The scanner contract
# --------------------------------------------------------------------------- #

#: Byte detectors from MASTERSPEC §7.3 that trade-off rules read.
BYTE_DETECTIONS: Final[tuple[str, ...]] = (
    "autoplay_media",
    "eager_below_fold",
    "no_reduced_motion",
    "text_in_image_suspected",
    "third_party_scripts",
)

#: Page facts the trade-off rules need that MASTERSPEC §7.3 does not list.
#: They save no bytes on their own, so the scanner records them with
#: ``Detection.saves_bytes = False``.
PAGE_FACT_DETECTIONS: Final[dict[str, str]] = {
    "div_soup_widgets": (
        "Clickable elements that are not buttons or links: div/span carrying "
        "onclick, or role=button on a non-button element."
    ),
    "no_color_scheme": (
        "No same-origin stylesheet contains a prefers-color-scheme rule and "
        "no color-scheme declaration is present, so the page has no dark mode."
    ),
    "video_no_captions": (
        "A video element with no track element of kind captions or subtitles."
    ),
    "lazy_above_fold": (
        "An image inside the first viewport carrying loading=lazy, which "
        "delays content the user is already looking at."
    ),
}

REQUIRED_DETECTIONS: Final[tuple[str, ...]] = tuple(
    sorted({*BYTE_DETECTIONS, *PAGE_FACT_DETECTIONS})
)


# --------------------------------------------------------------------------- #
# Small shared helpers
# --------------------------------------------------------------------------- #


def human_bytes(count: int) -> str:
    """Format a byte count for a card. Decimal units, matching the SWD model."""
    magnitude = abs(count)
    if magnitude < 1_000:
        return f"{count} B"
    if magnitude < 1_000_000:
        return f"{count / 1_000:.1f} KB"
    return f"{count / 1_000_000:.2f} MB"


def _detection(result: ScanResult, name: str) -> Detection | None:
    """The first scanner detection with this name, if the scanner recorded one."""
    for detection in result.carbon.detections:
        if detection.detector == name:
            return detection
    return None


def _images_with(result: ScanResult, issue: ImageIssueKind) -> list[ImageIssue]:
    return [image for image in result.carbon.images if issue in image.issues]


def _trim(values: Iterable[str]) -> list[str]:
    """Deduplicate, drop blanks, keep source order, cap the length."""
    seen: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
        if len(seen) >= MAX_EVIDENCE:
            break
    return seen


def _image_evidence(images: Sequence[ImageIssue]) -> list[str]:
    return _trim(image.selector or image.url for image in images)


def _count_fact(*candidates: int) -> int:
    """First non-zero count, so a detection's evidence can stand in for a list."""
    for candidate in candidates:
        if candidate:
            return candidate
    return 0


# --------------------------------------------------------------------------- #
# Synergy detectors
# --------------------------------------------------------------------------- #


def text_in_image(result: ScanResult) -> DetectorHit | None:
    """Images with text baked into the pixels (MASTERSPEC §7.3, §8.1 fix 11)."""
    images = _images_with(result, ImageIssueKind.TEXT_IN_IMAGE_SUSPECTED)
    detection = _detection(result, "text_in_image_suspected")
    if not images and detection is None:
        return None

    saving = (
        detection.estimated_saving_bytes
        if detection is not None
        else sum(image.estimated_saving_bytes for image in images)
    )
    evidence = _image_evidence(images) or _trim(detection.evidence if detection else [])
    count = _count_fact(len(images), len(detection.evidence) if detection else 0)

    return DetectorHit(
        evidence=evidence,
        bytes_delta=saving,
        facts={"count": str(count), "plural": "" if count == 1 else "s"},
    )


def autoplay_media(result: ScanResult) -> DetectorHit | None:
    """Autoplaying video or heavy animated GIFs (MASTERSPEC §7.3)."""
    media = [item for item in result.carbon.autoplay_media if item.autoplay or item.kind == "animated_gif"]
    detection = _detection(result, "autoplay_media")
    if not media and detection is None:
        return None

    saving = (
        detection.estimated_saving_bytes
        if detection is not None
        else sum(item.bytes for item in media)
    )
    evidence = _trim(item.selector or item.url for item in media) or _trim(
        detection.evidence if detection else []
    )
    count = _count_fact(len(media), len(detection.evidence) if detection else 0)

    return DetectorHit(
        evidence=evidence,
        bytes_delta=saving,
        facts={"count": str(count), "plural": "" if count == 1 else "s"},
    )


def no_reduced_motion(result: ScanResult) -> DetectorHit | None:
    """Animation with no `prefers-reduced-motion` escape hatch (MASTERSPEC §7.3)."""
    detection = _detection(result, "no_reduced_motion")
    if detection is None:
        return None
    return DetectorHit(
        evidence=_trim(detection.evidence),
        # Honouring the media query stops animation frames rendering; it changes
        # CPU and GPU work, not transfer size.
        bytes_delta=0,
        facts={"count": str(len(detection.evidence))},
    )


def eager_below_fold(result: ScanResult) -> DetectorHit | None:
    """Below-the-fold images loaded eagerly (MASTERSPEC §7.3)."""
    images = _images_with(result, ImageIssueKind.EAGER_BELOW_FOLD)
    detection = _detection(result, "eager_below_fold")
    if not images and detection is None:
        return None

    saving = (
        detection.estimated_saving_bytes
        if detection is not None
        else sum(image.bytes for image in images)
    )
    evidence = _image_evidence(images) or _trim(detection.evidence if detection else [])
    count = _count_fact(len(images), len(detection.evidence) if detection else 0)

    return DetectorHit(
        evidence=evidence,
        bytes_delta=saving,
        facts={"count": str(count), "plural": "" if count == 1 else "s"},
    )


def third_party_widgets(result: ScanResult) -> DetectorHit | None:
    """Scripts and widgets from other registrable domains (MASTERSPEC §7.3)."""
    third_party = result.carbon.third_party
    detection = _detection(result, "third_party_scripts")
    if not third_party.requests and detection is None:
        return None

    saving = (
        detection.estimated_saving_bytes
        if detection is not None
        else (third_party.script_bytes or third_party.bytes)
    )
    evidence = _trim(third_party.hosts) or _trim(detection.evidence if detection else [])

    return DetectorHit(
        evidence=evidence,
        bytes_delta=saving,
        facts={
            "count": str(third_party.requests),
            "hosts": ", ".join(evidence) or "other domains",
            "host_count": str(len(third_party.hosts)),
        },
    )


def div_soup_widgets(result: ScanResult) -> DetectorHit | None:
    """Clickable elements that are not buttons or links (MASTERSPEC §10)."""
    detection = _detection(result, "div_soup_widgets")
    if detection is None:
        return None
    return DetectorHit(
        evidence=_trim(detection.evidence),
        bytes_delta=detection.estimated_saving_bytes,
        facts={"count": str(len(detection.evidence))},
    )


# --------------------------------------------------------------------------- #
# Tension detectors
# --------------------------------------------------------------------------- #


def dark_mode(result: ScanResult) -> DetectorHit | None:
    """Page offers no dark mode at all (MASTERSPEC §10 tension).

    Fires only on the absence of `prefers-color-scheme` handling. A page that
    already honours the media query has made this call for itself, and the
    engine must not second-guess it.
    """
    if _detection(result, "no_color_scheme") is None:
        return None
    return DetectorHit(
        evidence=["document"],
        # Adding the token block and toggle costs bytes; it does not save any.
        bytes_delta=-DARK_MODE_CSS_BYTES,
        facts={"css_bytes": human_bytes(DARK_MODE_CSS_BYTES)},
    )


def captions_bytes(result: ScanResult) -> DetectorHit | None:
    """Video with no caption track (MASTERSPEC §10 tension, task: quantify cost)."""
    detection = _detection(result, "video_no_captions")
    if detection is None:
        return None

    targets = _trim(detection.evidence) or ["video"]
    video_count = max(1, len(detection.evidence))

    # Size the captions against the video they belong to, not the whole page.
    matched = [
        item
        for item in result.carbon.autoplay_media
        if item.kind == "video" and (item.selector in detection.evidence or item.url in detection.evidence)
    ]
    video_bytes = sum(item.bytes for item in matched) or sum(
        item.bytes for item in result.carbon.autoplay_media if item.kind == "video"
    )

    caption_bytes = WEBVTT_BYTES_PER_VIDEO * video_count
    share = f"{caption_bytes / video_bytes * 100:.2f}%" if video_bytes > 0 else "an unknown share of"

    return DetectorHit(
        evidence=targets,
        bytes_delta=-caption_bytes,
        facts={
            "count": str(video_count),
            "plural": "" if video_count == 1 else "s",
            "caption_bytes": human_bytes(caption_bytes),
            "video_bytes": human_bytes(video_bytes),
            "net_bytes": human_bytes(video_bytes + caption_bytes),
            "share": share,
        },
    )


def lazy_above_fold(result: ScanResult) -> DetectorHit | None:
    """Images in the first viewport marked `loading="lazy"` (MASTERSPEC §10)."""
    detection = _detection(result, "lazy_above_fold")
    if detection is None:
        return None
    return DetectorHit(
        evidence=_trim(detection.evidence),
        # The bytes still arrive; only their timing changes. Removing the
        # attribute neither saves nor costs transfer.
        bytes_delta=0,
        facts={"count": str(len(detection.evidence))},
    )


def high_res_zoom(result: ScanResult) -> DetectorHit | None:
    """Images with no resolution headroom left for zoom (MASTERSPEC §10).

    Computed straight from the scan's image table: no extra detection needed.
    """
    at_risk = [
        image
        for image in result.carbon.images
        if image.rendered_w >= ZOOM_MIN_RENDERED_WIDTH_PX
        and image.natural_w > 0
        and image.natural_w < image.rendered_w * ZOOM_HEADROOM_FACTOR
    ]
    if not at_risk:
        return None

    return DetectorHit(
        evidence=_image_evidence(at_risk),
        # The finding exists to stop a saving being taken, not to take one.
        bytes_delta=0,
        facts={
            "count": str(len(at_risk)),
            "plural": "" if len(at_risk) == 1 else "s",
            "factor": f"{ZOOM_HEADROOM_FACTOR:g}",
        },
    )


def font_subsetting(result: ScanResult) -> DetectorHit | None:
    """A font payload big enough that subsetting will be proposed (MASTERSPEC §10)."""
    fonts = result.carbon.fonts
    if fonts.count < FONT_COUNT_THRESHOLD or fonts.bytes <= FONT_BYTES_THRESHOLD:
        return None

    return DetectorHit(
        evidence=_trim(fonts.urls),
        # Subsetting safely (dropping unused weights) is the fix; how many bytes
        # that frees depends on which ranges the page truly needs, so the
        # finding claims no number rather than inventing one.
        bytes_delta=0,
        facts={
            "count": str(fonts.count),
            "font_bytes": human_bytes(fonts.bytes),
        },
    )


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

#: Name in ``rules.json`` -> detector function.
REGISTRY: Final[dict[str, Callable[[ScanResult], DetectorHit | None]]] = {
    "autoplay_media": autoplay_media,
    "captions_bytes": captions_bytes,
    "dark_mode": dark_mode,
    "div_soup_widgets": div_soup_widgets,
    "eager_below_fold": eager_below_fold,
    "font_subsetting": font_subsetting,
    "high_res_zoom": high_res_zoom,
    "lazy_above_fold": lazy_above_fold,
    "no_reduced_motion": no_reduced_motion,
    "text_in_image": text_in_image,
    "third_party_widgets": third_party_widgets,
}

#: Fill-ins each detector guarantees, on top of the ones the engine always
#: adds (see ``engine.ENGINE_FACTS``). A test asserts every placeholder in
#: ``rules.json`` is covered by one of the two sets, so a template can never
#: reach the UI with a hole in it.
DETECTOR_FACTS: Final[dict[str, frozenset[str]]] = {
    "autoplay_media": frozenset({"count", "plural"}),
    "captions_bytes": frozenset(
        {"count", "plural", "caption_bytes", "video_bytes", "net_bytes", "share"}
    ),
    "dark_mode": frozenset({"css_bytes"}),
    "div_soup_widgets": frozenset({"count"}),
    "eager_below_fold": frozenset({"count", "plural"}),
    "font_subsetting": frozenset({"count", "font_bytes"}),
    "high_res_zoom": frozenset({"count", "plural", "factor"}),
    "lazy_above_fold": frozenset({"count"}),
    "no_reduced_motion": frozenset({"count"}),
    "text_in_image": frozenset({"count", "plural"}),
    "third_party_widgets": frozenset({"count", "hosts", "host_count"}),
}
