"""Pydantic models for every structure in MASTERSPEC §5.

These are the contract between the scanner, the API and the frontend. The
TypeScript mirror lives in ``frontend/src/lib/types.ts`` (CLAUDE.md).

One addition to the spec: :class:`Scores` carries ``is_placeholder``. The
scoring module does not exist yet, and CLAUDE.md forbids presenting invented
numbers as real, so a result produced before scoring exists says so in the
payload itself rather than quietly shipping zeros that look computed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #


class Impact(StrEnum):
    """axe-core impact levels, ordered most to least severe."""

    CRITICAL = "critical"
    SERIOUS = "serious"
    MODERATE = "moderate"
    MINOR = "minor"


class CarbonGrade(StrEnum):
    """Sustainable Web Design rating bands (MASTERSPEC §9.2)."""

    A_PLUS = "A+"
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    F = "F"


class ScanStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class ResourceType(StrEnum):
    """CDP resourceType mapped to the buckets MASTERSPEC §6.2 names."""

    HTML = "html"
    CSS = "css"
    JS = "js"
    IMG = "img"
    FONT = "font"
    MEDIA = "media"
    OTHER = "other"


class TradeoffType(StrEnum):
    SYNERGY = "synergy"
    TENSION = "tension"


class GreenSource(StrEnum):
    GREENWEB = "greenweb"
    UNAVAILABLE = "unavailable"


class ImageIssueKind(StrEnum):
    """The issue labels MASTERSPEC §5 lists for ImageIssue."""

    OVERSIZED = "oversized"
    LEGACY_FORMAT = "legacy_format"
    NO_DIMENSIONS = "no_dimensions"
    EAGER_BELOW_FOLD = "eager_below_fold"
    TEXT_IN_IMAGE_SUSPECTED = "text_in_image_suspected"


class StepStatus(StrEnum):
    RUNNING = "running"
    OK = "ok"
    ERROR = "error"
    SKIPPED = "skipped"


class ErrorCode(StrEnum):
    """Error codes from MASTERSPEC §12."""

    URL_BLOCKED = "URL_BLOCKED"
    TIMEOUT = "TIMEOUT"
    PAGE_TOO_LARGE = "PAGE_TOO_LARGE"
    NAV_FAILED = "NAV_FAILED"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    PATCH_FAILED = "PATCH_FAILED"


class _Model(BaseModel):
    """Shared configuration for every model here."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)


# --------------------------------------------------------------------------- #
# Request
# --------------------------------------------------------------------------- #


class Weights(_Model):
    """Score weighting (MASTERSPEC §9.3). Defaults to an even split."""

    a11y: float = Field(default=0.5, ge=0.0, le=1.0)
    carbon: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("carbon")
    @classmethod
    def _must_sum_to_one(cls, carbon: float, info) -> float:  # noqa: ANN001
        a11y = info.data.get("a11y")
        if a11y is not None and abs((a11y + carbon) - 1.0) > 1e-6:
            raise ValueError(f"weights must sum to 1.0, got a11y={a11y} carbon={carbon}")
        return carbon


class ScanRequest(_Model):
    url: HttpUrl
    weights: Weights | None = None


# --------------------------------------------------------------------------- #
# Accessibility
# --------------------------------------------------------------------------- #


class ViolationNode(_Model):
    """One element that failed a rule."""

    selector: str
    html: str
    failure_summary: str = ""


class Violation(_Model):
    """One axe-core rule that failed, with every element that failed it."""

    rule_id: str
    impact: Impact
    help: str
    help_url: str = ""
    tags: list[str] = Field(default_factory=list)
    nodes: list[ViolationNode] = Field(default_factory=list)

    @property
    def node_count(self) -> int:
        return len(self.nodes)


class A11yResult(_Model):
    violations: list[Violation] = Field(default_factory=list)
    counts_by_impact: dict[Impact, int] = Field(default_factory=dict)
    unique_rules: int = 0
    total_nodes: int = 0
    #: axe "incomplete" results are recorded but never scored (MASTERSPEC §6.3).
    incomplete_count: int = 0


# --------------------------------------------------------------------------- #
# Keyboard
# --------------------------------------------------------------------------- #


class KeyboardResult(_Model):
    """Results of our own Tab crawl (MASTERSPEC §6.4).

    Not an axe result. The UI must attribute it to "automated keyboard crawl".
    """

    tabs_pressed: int = 0
    trap_detected: bool = False
    trap_container: str | None = None
    unreachable_interactive_count: int = 0
    focus_visible_missing_count: int = 0
    #: Distinct elements the crawl actually reached, for transparency.
    reached_count: int = 0


# --------------------------------------------------------------------------- #
# Carbon
# --------------------------------------------------------------------------- #


class ImageIssue(_Model):
    url: str
    bytes: int = 0
    natural_w: int = 0
    natural_h: int = 0
    rendered_w: int = 0
    rendered_h: int = 0
    format: str = ""
    issues: list[ImageIssueKind] = Field(default_factory=list)
    selector: str = ""
    #: Estimated bytes saved if every issue on this image were addressed.
    estimated_saving_bytes: int = 0


class MediaItem(_Model):
    """An autoplaying video or a heavy animated GIF (MASTERSPEC §7.3)."""

    url: str
    bytes: int = 0
    selector: str = ""
    kind: Literal["video", "animated_gif"] = "video"
    autoplay: bool = False
    loop: bool = False
    muted: bool = False
    has_poster: bool = False


class UncompressedResource(_Model):
    url: str
    resource_type: ResourceType
    #: Bytes actually transferred.
    transfer_bytes: int = 0
    #: Bytes after decoding; equal to transfer_bytes when nothing was applied.
    decoded_bytes: int = 0
    content_encoding: str = ""
    estimated_saving_bytes: int = 0


class ThirdPartySummary(_Model):
    requests: int = 0
    bytes: int = 0
    hosts: list[str] = Field(default_factory=list)
    script_bytes: int = 0


class FontSummary(_Model):
    count: int = 0
    bytes: int = 0
    urls: list[str] = Field(default_factory=list)


class CarbonAssumptions(_Model):
    """Every assumption behind the grams figure, surfaced for the UI popover."""

    model: str = "sustainable-web-design"
    model_version: str = "3"
    kwh_per_gb: float = 0.0
    grid_intensity_g_per_kwh: float = 0.0
    renewable_intensity_g_per_kwh: float = 0.0
    first_visit_percentage: float = 0.0
    return_visit_percentage: float = 0.0
    data_reload_ratio: float = 0.0
    green_hosted: bool = False
    segment_shares: dict[str, float] = Field(default_factory=dict)
    source_url: str = ""


class Detection(_Model):
    """One detector finding (MASTERSPEC §7.3)."""

    detector: str
    #: Human-readable summary of what was found.
    summary: str
    #: Estimated bytes saved. Always an estimate, never a measurement.
    estimated_saving_bytes: int = 0
    #: Selectors or URLs that evidence the finding.
    evidence: list[str] = Field(default_factory=list)
    #: Some detectors (no_dimensions, no_reduced_motion) save no bytes.
    saves_bytes: bool = True


class CarbonResult(_Model):
    total_bytes: int = 0
    request_count: int = 0
    by_type: dict[ResourceType, int] = Field(default_factory=dict)
    third_party: ThirdPartySummary = Field(default_factory=ThirdPartySummary)
    images: list[ImageIssue] = Field(default_factory=list)
    autoplay_media: list[MediaItem] = Field(default_factory=list)
    fonts: FontSummary = Field(default_factory=FontSummary)
    uncompressed_text: list[UncompressedResource] = Field(default_factory=list)
    detections: list[Detection] = Field(default_factory=list)

    grams_per_view: float = 0.0
    grams_first_visit: float = 0.0
    grams_return_visit: float = 0.0

    assumptions: CarbonAssumptions = Field(default_factory=CarbonAssumptions)
    #: Always true. Carbon figures are modelled, never measured (MASTERSPEC §7).
    is_estimate: Literal[True] = True


class GreenResult(_Model):
    host: str
    green: bool = False
    hosted_by: str | None = None
    source: GreenSource = GreenSource.UNAVAILABLE


# --------------------------------------------------------------------------- #
# Scores and trade-offs
# --------------------------------------------------------------------------- #


class Scores(_Model):
    a11y: int = Field(default=0, ge=0, le=100)
    carbon: int = Field(default=0, ge=0, le=100)
    combined: int = Field(default=0, ge=0, le=100)
    carbon_grade: CarbonGrade = CarbonGrade.F
    weights: Weights = Field(default_factory=Weights)
    #: True until the scoring module exists. Addition to MASTERSPEC §5 so that
    #: a pre-scoring result cannot be mistaken for a computed one.
    is_placeholder: bool = False


class TradeoffFinding(_Model):
    rule_id: str
    type: TradeoffType
    title: str
    a11y_impact: str
    carbon_delta_bytes: int = 0
    carbon_delta_grams: float = 0.0
    evidence: list[str] = Field(default_factory=list)
    recommended_fix_id: str | None = None
    explanation: str = ""


# --------------------------------------------------------------------------- #
# Fixes
# --------------------------------------------------------------------------- #


class FixDiff(_Model):
    before: str = ""
    after: str = ""


class Fix(_Model):
    id: str
    kind: str
    target: str
    description: str
    diff: FixDiff = Field(default_factory=FixDiff)
    ai_generated: bool = False
    applied: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    manual_review: bool = False


class SkippedFix(_Model):
    fix_id: str
    reason: str


class PatchInfo(_Model):
    fixes: list[Fix] = Field(default_factory=list)
    zip_path: str | None = None
    patched_url: str | None = None
    skipped: list[SkippedFix] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #


class EngineVersions(_Model):
    playwright: str
    axe: str
    swd_model: str


class ScanResult(_Model):
    scores: Scores = Field(default_factory=Scores)
    a11y: A11yResult = Field(default_factory=A11yResult)
    keyboard: KeyboardResult = Field(default_factory=KeyboardResult)
    carbon: CarbonResult = Field(default_factory=CarbonResult)
    green: GreenResult
    tradeoffs: list[TradeoffFinding] = Field(default_factory=list)
    aria_snapshot: str = ""
    screenshot_path: str = ""
    engine_versions: EngineVersions


class Scan(_Model):
    id: str
    url: str
    host: str
    status: ScanStatus = ScanStatus.QUEUED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    before: ScanResult | None = None
    after: ScanResult | None = None
    patch: PatchInfo | None = None


# --------------------------------------------------------------------------- #
# Pipeline events (MASTERSPEC §12 SSE)
# --------------------------------------------------------------------------- #


class StepEvent(_Model):
    """One pipeline step transition, emitted by the scan pipeline."""

    name: str
    status: StepStatus
    ms: int = 0
    detail: str = ""


class ApiError(_Model):
    code: ErrorCode
    message: str


class ApiErrorEnvelope(_Model):
    """The API error shape from MASTERSPEC §12: ``{error: {code, message}}``."""

    error: ApiError
