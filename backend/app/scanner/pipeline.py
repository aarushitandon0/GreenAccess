"""Scan orchestration (MASTERSPEC §4).

Runs the scan steps in the order §4 lays out and reports progress as an async
generator of :class:`~app.models.StepEvent`. The API job runner forwards those
straight down an SSE stream (MASTERSPEC §12); the CLI prints them. Neither has
to know how a scan works.

Steps, in order::

    validate  SSRF guard
    load      new context, CDP enabled, navigate, scroll, screenshot
    a11y      vendored axe-core
    keyboard  Tab crawl
    aria      accessibility-tree snapshot
    carbon    aggregate network data, run detectors, compute grams
    green     Green Web Foundation lookup
    score     three pure score functions (MASTERSPEC §9)
    tradeoffs rules-driven synergy and tension findings (MASTERSPEC §10)

The pipeline is storage-agnostic, so the tenth step, ``persist``, belongs to
the caller: :mod:`app.api.runner` saves the result and reports ``persist``
itself. The CLI does not persist and so reports no such step.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

from app.carbon import detectors, swd
from app.carbon.constants import (
    FIRST_TIME_VIEWING_PERCENTAGE,
    GLOBAL_GRID_INTENSITY,
    KWH_PER_GB,
    PERCENTAGE_OF_DATA_LOADED_ON_SUBSEQUENT_LOAD,
    RENEWABLES_GRID_INTENSITY,
    RETURNING_VISITOR_PERCENTAGE,
    SWD_SOURCE_URL,
)
from app.carbon.green import check_green_hosting
from app.config import Settings, get_settings
from app.models import (
    A11yResult,
    CarbonAssumptions,
    CarbonResult,
    EngineVersions,
    GreenResult,
    KeyboardResult,
    ScanResult,
    StepEvent,
    StepStatus,
    Weights,
)
from app.scanner import a11y as a11y_module
from app.scanner import aria as aria_module
from app.scanner import dom as dom_module
from app.scanner import keyboard as keyboard_module
from app.scanner.browser import BrowserSession, LoadResult, ScanAborted
from app.scoring.combined import compute_scores
from app.security.ssrf import UrlBlocked, validate_url
from app.tradeoffs.engine import evaluate as evaluate_tradeoffs
from app.version import engine_versions

logger = logging.getLogger(__name__)


class ScanFailed(Exception):
    """A scan could not complete. `code` maps to MASTERSPEC §12's error codes."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass
class ScanPipeline:
    """One scan. Iterate :meth:`run` for progress; read :attr:`result` after.

    Usage::

        pipeline = ScanPipeline(url)
        async for event in pipeline.run():
            ...
        result = pipeline.result
    """

    url: str
    settings: Settings = field(default_factory=get_settings)
    screenshot_dir: Path | None = None
    #: Score weighting (MASTERSPEC §9.3). Defaults to the even split.
    weights: Weights = field(default_factory=Weights)

    result: ScanResult | None = None
    load_result: LoadResult | None = None
    error: ScanFailed | None = None

    async def run(self) -> AsyncIterator[StepEvent]:
        """Execute the scan, yielding a StepEvent as each step starts and ends."""
        started = time.monotonic()

        # -- validate ----------------------------------------------------- #
        async for event in self._step("validate"):
            yield event
        try:
            # DNS resolution blocks, and the API serves other scans on this loop.
            validated = await asyncio.to_thread(
                validate_url, self.url, allowed_local_hosts=self.settings.allowed_local_hosts
            )
        except UrlBlocked as blocked:
            self.error = ScanFailed("URL_BLOCKED", blocked.reason)
            yield StepEvent(
                name="validate",
                status=StepStatus.ERROR,
                ms=self._ms(started),
                detail=blocked.reason,
            )
            raise self.error from blocked
        yield StepEvent(
            name="validate",
            status=StepStatus.OK,
            ms=self._ms(started),
            detail=f"{validated.host} -> {', '.join(validated.resolved_ips) or 'n/a'}",
        )

        a11y_result = A11yResult()
        keyboard_result = KeyboardResult()
        aria_snapshot = ""
        carbon_result = CarbonResult()
        green_result = GreenResult(host=validated.host)

        async with BrowserSession(
            nav_timeout_s=self.settings.nav_timeout_s,
            max_page_bytes=self.settings.max_page_bytes,
            allowed_local_hosts=self.settings.allowed_local_hosts,
            screenshot_dir=self.screenshot_dir,
        ) as session:
            # -- load ----------------------------------------------------- #
            step_started = time.monotonic()
            yield StepEvent(name="load", status=StepStatus.RUNNING)
            try:
                self.load_result = await session.load(validated.url)
            except ScanAborted as aborted:
                self.error = ScanFailed(aborted.code, str(aborted))
                yield StepEvent(
                    name="load",
                    status=StepStatus.ERROR,
                    ms=self._ms(step_started),
                    detail=str(aborted),
                )
                raise self.error from aborted
            yield StepEvent(
                name="load",
                status=StepStatus.OK,
                ms=self._ms(step_started),
                detail=(
                    f"HTTP {self.load_result.status}, "
                    f"{self.load_result.scroll_steps} scroll step(s)"
                ),
            )

            # -- a11y ----------------------------------------------------- #
            step_started = time.monotonic()
            yield StepEvent(name="a11y", status=StepStatus.RUNNING)
            try:
                a11y_result = await a11y_module.run_axe(session.page)
            except (a11y_module.AxeUnavailable, TimeoutError, OSError) as exc:
                # Carrying on would score an empty violation list as 100, a
                # number nobody measured (CLAUDE.md: never fake results).
                logger.warning("a11y step failed: %s", exc)
                code = "TIMEOUT" if isinstance(exc, TimeoutError) else "NAV_FAILED"
                self.error = ScanFailed(code, f"accessibility audit could not run: {exc}")
                yield StepEvent(
                    name="a11y",
                    status=StepStatus.ERROR,
                    ms=self._ms(step_started),
                    detail=str(exc),
                )
                raise self.error from exc
            yield StepEvent(
                name="a11y",
                status=StepStatus.OK,
                ms=self._ms(step_started),
                detail=f"{a11y_result.unique_rules} rule(s), {a11y_result.total_nodes} node(s)",
            )

            # -- keyboard ------------------------------------------------- #
            step_started = time.monotonic()
            yield StepEvent(name="keyboard", status=StepStatus.RUNNING)
            keyboard_result = await keyboard_module.crawl(session.page)
            yield StepEvent(
                name="keyboard",
                status=StepStatus.OK,
                ms=self._ms(step_started),
                detail=(
                    f"{keyboard_result.tabs_pressed} tab(s), "
                    f"trap={'yes' if keyboard_result.trap_detected else 'no'}"
                ),
            )

            # -- aria ----------------------------------------------------- #
            step_started = time.monotonic()
            yield StepEvent(name="aria", status=StepStatus.RUNNING)
            aria_snapshot = await aria_module.snapshot(session.page)
            yield StepEvent(
                name="aria",
                status=StepStatus.OK,
                ms=self._ms(step_started),
                detail=f"{len(aria_snapshot):,} characters",
            )

            # -- carbon --------------------------------------------------- #
            step_started = time.monotonic()
            yield StepEvent(name="carbon", status=StepStatus.RUNNING)
            dom_facts = await dom_module.collect(session.page)
            network = session.collector.summarize(self.load_result.final_url)
            carbon_result = self._build_carbon(dom_facts, network, green=False)
            yield StepEvent(
                name="carbon",
                status=StepStatus.OK,
                ms=self._ms(step_started),
                detail=(
                    f"{carbon_result.total_bytes:,} bytes over "
                    f"{carbon_result.request_count} request(s), "
                    f"{len(carbon_result.detections)} finding(s)"
                ),
            )

        # -- green (outside the browser session) -------------------------- #
        step_started = time.monotonic()
        yield StepEvent(name="green", status=StepStatus.RUNNING)
        green_result = await check_green_hosting(validated.host)
        yield StepEvent(
            name="green",
            status=StepStatus.OK,
            ms=self._ms(step_started),
            detail=(
                "unknown"
                if green_result.source.value == "unavailable"
                else ("green" if green_result.green else "not green")
            ),
        )

        # The grams figure depends on whether the host is green, so recompute
        # now that we know. Only the data-centre segment changes.
        if green_result.green:
            carbon_result = self._recompute_grams(carbon_result, green=True)

        # -- score ---------------------------------------------------------- #
        step_started = time.monotonic()
        yield StepEvent(name="score", status=StepStatus.RUNNING)
        scores = compute_scores(
            a11y=a11y_result,
            keyboard=keyboard_result,
            carbon=carbon_result,
            green=green_result,
            weights=self.weights,
        )
        yield StepEvent(
            name="score",
            status=StepStatus.OK,
            ms=self._ms(step_started),
            detail=(
                f"a11y {scores.a11y}, carbon {scores.carbon} ({scores.carbon_grade.value}), "
                f"combined {scores.combined}"
            ),
        )

        self.result = ScanResult(
            scores=scores,
            a11y=a11y_result,
            keyboard=keyboard_result,
            carbon=carbon_result,
            green=green_result,
            tradeoffs=[],
            aria_snapshot=aria_snapshot,
            screenshot_path=self.load_result.screenshot_path if self.load_result else "",
            engine_versions=EngineVersions(**engine_versions()),
        )

        # -- tradeoffs ------------------------------------------------------ #
        # Runs against the assembled result, so every detector reads exactly
        # what the API will serve. No page loads, no network, no LLM.
        step_started = time.monotonic()
        yield StepEvent(name="tradeoffs", status=StepStatus.RUNNING)
        self.result.tradeoffs = evaluate_tradeoffs(self.result)
        synergies = sum(1 for f in self.result.tradeoffs if f.type.value == "synergy")
        tensions = len(self.result.tradeoffs) - synergies
        yield StepEvent(
            name="tradeoffs",
            status=StepStatus.OK,
            ms=self._ms(step_started),
            detail=f"{synergies} synergy, {tensions} tension",
        )

    # -- helpers ---------------------------------------------------------- #

    @staticmethod
    def _ms(started: float) -> int:
        return int((time.monotonic() - started) * 1000)

    async def _step(self, name: str) -> AsyncIterator[StepEvent]:
        yield StepEvent(name=name, status=StepStatus.RUNNING)

    def _assumptions(self, *, green: bool) -> CarbonAssumptions:
        return CarbonAssumptions(
            model="sustainable-web-design",
            model_version=swd.MODEL_VERSION,
            kwh_per_gb=KWH_PER_GB,
            grid_intensity_g_per_kwh=GLOBAL_GRID_INTENSITY,
            renewable_intensity_g_per_kwh=RENEWABLES_GRID_INTENSITY,
            first_visit_percentage=FIRST_TIME_VIEWING_PERCENTAGE,
            return_visit_percentage=RETURNING_VISITOR_PERCENTAGE,
            data_reload_ratio=PERCENTAGE_OF_DATA_LOADED_ON_SUBSEQUENT_LOAD,
            green_hosted=green,
            segment_shares=swd.segment_shares(),
            source_url=SWD_SOURCE_URL,
        )

    def _build_carbon(self, dom_facts, network, *, green: bool) -> CarbonResult:  # noqa: ANN001
        total = network.total_bytes
        return CarbonResult(
            total_bytes=total,
            request_count=network.request_count,
            by_type=dict(network.by_type),
            third_party=network.third_party,
            images=detectors.build_image_issues(dom_facts, network),
            autoplay_media=detectors.build_media_items(dom_facts, network),
            fonts=network.fonts,
            uncompressed_text=list(network.uncompressed_text),
            detections=detectors.run_all(dom_facts, network),
            grams_per_view=swd.per_visit(total, green=green),
            grams_first_visit=swd.per_byte(total, green=green),
            grams_return_visit=swd.grams_return_visit(total, green=green),
            assumptions=self._assumptions(green=green),
        )

    def _recompute_grams(self, carbon: CarbonResult, *, green: bool) -> CarbonResult:
        """Re-derive the grams figures once green hosting is known."""
        return carbon.model_copy(
            update={
                "grams_per_view": swd.per_visit(carbon.total_bytes, green=green),
                "grams_first_visit": swd.per_byte(carbon.total_bytes, green=green),
                "grams_return_visit": swd.grams_return_visit(carbon.total_bytes, green=green),
                "assumptions": self._assumptions(green=green),
            }
        )


async def run_scan(
    url: str,
    *,
    settings: Settings | None = None,
    screenshot_dir: Path | None = None,
) -> tuple[list[StepEvent], ScanResult | None]:
    """Convenience wrapper: run a scan to completion and collect its events."""
    pipeline = ScanPipeline(
        url=url, settings=settings or get_settings(), screenshot_dir=screenshot_dir
    )
    events: list[StepEvent] = []
    async for event in pipeline.run():
        events.append(event)
    return events, pipeline.result
