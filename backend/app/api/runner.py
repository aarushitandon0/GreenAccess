"""Scan job runner (MASTERSPEC §4 pipeline, §12 SSE, §17 limits).

Single responsibility: run each accepted scan as its own asyncio task, at most
``MAX_CONCURRENT_SCANS`` at a time, inside ``SCAN_TIMEOUT_S``, publishing every
step to the scan's event log and the outcome to the database.

Guarantees, because a hung progress screen is the worst failure a demo can
have:

* Every scan ends with exactly one terminal event, ``done`` or ``error``, and
  its event log is closed, whatever happened: pipeline failure, timeout,
  cancellation, a bug, or a database error while recording the failure.
* The timeout covers the scan's own work, not time spent queued behind others.
* On cancel or timeout the pipeline generator is closed explicitly, so its
  ``async with BrowserSession`` exits and Chromium is shut down.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.api.events import EventHub, ScanEventLog
from app.config import Settings
from app.db.repository import ScanRepository
from app.models import (
    ApiError,
    DoneEvent,
    ErrorCode,
    Scan,
    ScanResult,
    ScanStatus,
    StepEvent,
    StepStatus,
    Weights,
)
from app.scanner.pipeline import ScanFailed, ScanPipeline

logger = logging.getLogger(__name__)

__all__ = ["JobFactory", "JobSpec", "ScanJob", "ScanRunner", "pipeline_job"]


@dataclass(frozen=True)
class JobSpec:
    """Everything a scan job needs to run."""

    scan_id: str
    url: str
    weights: Weights
    settings: Settings
    screenshot_dir: Path


class ScanJob(Protocol):
    """The part of :class:`~app.scanner.pipeline.ScanPipeline` the runner uses."""

    result: ScanResult | None

    def run(self) -> AsyncIterator[StepEvent]: ...


JobFactory = Callable[[JobSpec], ScanJob]


def pipeline_job(spec: JobSpec) -> ScanJob:
    """The real job: the full scanner pipeline."""
    return ScanPipeline(
        url=spec.url,
        settings=spec.settings,
        screenshot_dir=spec.screenshot_dir,
        weights=spec.weights,
    )


def _error_code(raw: str) -> ErrorCode:
    try:
        return ErrorCode(raw)
    except ValueError:
        logger.warning("pipeline reported unknown error code %r", raw)
        return ErrorCode.NAV_FAILED


class ScanRunner:
    """Owns the scan tasks and the concurrency limit."""

    def __init__(
        self,
        *,
        settings: Settings,
        repository: ScanRepository,
        hub: EventHub,
        job_factory: JobFactory = pipeline_job,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.hub = hub
        self.job_factory = job_factory
        self._slots = asyncio.Semaphore(max(1, settings.max_concurrent_scans))
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._running: set[str] = set()
        self._cancel_reasons: dict[str, str] = {}

    # -- introspection ---------------------------------------------------- #

    @property
    def running(self) -> frozenset[str]:
        """Scans that hold a slot right now."""
        return frozenset(self._running)

    @property
    def queued(self) -> frozenset[str]:
        """Scans accepted but waiting for a slot."""
        return frozenset(self._tasks) - self._running

    def is_active(self, scan_id: str) -> bool:
        return scan_id in self._tasks

    @property
    def slots(self) -> asyncio.Semaphore:
        """The MAX_CONCURRENT_SCANS limit, shared with patch re-scans."""
        return self._slots

    # -- control ---------------------------------------------------------- #

    async def submit(self, scan: Scan, weights: Weights) -> None:
        """Record the scan as queued and start its task."""
        await self.repository.insert(scan)
        log = self.hub.create(scan.id)
        task = asyncio.create_task(self._run(scan, weights, log), name=f"scan-{scan.id}")
        self._tasks[scan.id] = task
        task.add_done_callback(lambda _: self._tasks.pop(scan.id, None))

    async def cancel(self, scan_id: str, reason: str = "cancelled by request") -> bool:
        """Cancel a queued or running scan and wait for it to record that.

        Returns False if the scan is not active (already finished, or unknown).
        """
        task = self._tasks.get(scan_id)
        if task is None:
            return False
        self._cancel_reasons.setdefault(scan_id, reason)
        task.cancel()
        # The task records CANCELLED itself; wait so the caller reads the result.
        await asyncio.wait({task}, timeout=10)
        return True

    async def shutdown(self) -> None:
        """Cancel everything still running (application shutdown)."""
        tasks = list(self._tasks.items())
        for scan_id, task in tasks:
            self._cancel_reasons.setdefault(scan_id, "the server shut down during the scan")
            task.cancel()
        if tasks:
            await asyncio.wait([task for _, task in tasks], timeout=15)

    # -- the task --------------------------------------------------------- #

    async def _run(self, scan: Scan, weights: Weights, log: ScanEventLog) -> None:
        error: ApiError | None = None
        try:
            async with self._slots:
                self._running.add(scan.id)
                await self.repository.mark_running(scan.id)
                async with asyncio.timeout(self.settings.scan_timeout_s):
                    result = await self._execute(scan, weights, log)
                await self._persist(scan.id, result, log)
        except asyncio.CancelledError:
            reason = self._cancel_reasons.get(scan.id, "the scan was cancelled")
            error = ApiError(code=ErrorCode.CANCELLED, message=reason)
            # Not re-raised: the task is ours, and ending it is the cancellation.
        except TimeoutError:
            error = ApiError(
                code=ErrorCode.TIMEOUT,
                message=f"the scan did not finish within {self.settings.scan_timeout_s} s",
            )
        except ScanFailed as failure:
            error = ApiError(code=_error_code(failure.code), message=str(failure))
        except Exception:
            # Task boundary: a bug must fail the scan, never hang its stream.
            logger.exception("scan %s failed unexpectedly", scan.id)
            error = ApiError(
                code=ErrorCode.NAV_FAILED,
                message="the scan stopped because of an internal error; see the server log",
            )
        finally:
            self._running.discard(scan.id)
            self._cancel_reasons.pop(scan.id, None)
            try:
                if error is not None:
                    await self._record_failure(scan.id, error, log)
            finally:
                await log.close()

    async def _execute(self, scan: Scan, weights: Weights, log: ScanEventLog) -> ScanResult:
        job = self.job_factory(
            JobSpec(
                scan_id=scan.id,
                url=scan.url,
                weights=weights,
                settings=self.settings,
                screenshot_dir=self.settings.screenshot_dir / scan.id,
            )
        )
        # aclosing: on cancel or timeout the generator is closed here, which
        # runs the pipeline's `async with BrowserSession` exit and frees Chromium.
        async with contextlib.aclosing(job.run()) as steps:
            async for step in steps:
                await log.publish("step", step)
        if job.result is None:
            raise ScanFailed(ErrorCode.NAV_FAILED.value, "the scan produced no result")
        return job.result

    async def _persist(self, scan_id: str, result: ScanResult, log: ScanEventLog) -> None:
        started = time.monotonic()
        await log.publish("step", StepEvent(name="persist", status=StepStatus.RUNNING))
        await self.repository.save_before(scan_id, result)
        await log.publish(
            "step",
            StepEvent(
                name="persist",
                status=StepStatus.OK,
                ms=int((time.monotonic() - started) * 1000),
            ),
        )
        # Only after the row is written, so a client that reacts to `done` by
        # fetching the scan always finds the result.
        await log.publish("done", DoneEvent(scan_id=scan_id, status=ScanStatus.DONE))

    async def _record_failure(self, scan_id: str, error: ApiError, log: ScanEventLog) -> None:
        try:
            await self.repository.mark_error(scan_id, error)
        except Exception:
            # The stream must still end even if the database is down.
            logger.exception("could not record failure of scan %s", scan_id)
        await log.publish("error", error)
