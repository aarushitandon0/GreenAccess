"""Fix pipeline routes and job (MASTERSPEC §4 fix pipeline, §8, §12).

Single responsibility: the HTTP surface and orchestration for "generate fixes
→ accept → patch → serve patched copy → re-scan → After result".

    POST /api/scans/{id}/fixes      -> FixesResponse   (SSE step ``fixes``)
    POST /api/scans/{id}/patch      -> 202 PatchAccepted (SSE ``patch``, ``rescan``)
    GET  /api/scans/{id}/patch.zip  -> application/zip
    GET  /patched/{id}/{path}       -> static file, with the preview CSP

The re-scan runs through the same job factory as every scan, so the After
numbers come from exactly the pipeline that produced the Before ones
(CLAUDE.md: "The score after fixes must come from a real re-scan"). It takes
one of the ``MAX_CONCURRENT_SCANS`` slots and is bounded by ``SCAN_TIMEOUT_S``.

Each phase on the event stream ends with its own ``done`` or ``error``, and
the patch job, like the scan job, always ends with exactly one of them.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import mimetypes
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Response
from fastapi.responses import FileResponse

from app.api.errors import ApiException
from app.api.events import ScanEventLog
from app.api.runner import JobSpec, ScanRunner
from app.api.state import AppState, get_state
from app.config import Settings
from app.db.repository import ScanRepository
from app.llm.client import LlmClient
from app.models import (
    ApiError,
    ApiErrorEnvelope,
    DoneEvent,
    ErrorCode,
    FixesResponse,
    PatchAccepted,
    PatchInfo,
    PatchRequest,
    Scan,
    ScanStatus,
    StepEvent,
    StepStatus,
    Weights,
)
from app.patcher.build import build_patch
from app.patcher.fixgen import fetch_source, generate_plan
from app.patcher.plan import load_plan, save_plan
from app.patcher.rescan import carry_hosting
from app.scanner.pipeline import ScanFailed
from app.security.fetch import FetchError, SafeFetcher

logger = logging.getLogger(__name__)

__all__ = ["LlmFactory", "PatchRunner", "patched_router", "router"]

router = APIRouter(prefix="/api", tags=["fixes"])
patched_router = APIRouter(tags=["patched"])

State = Annotated[AppState, Depends(get_state)]

LlmFactory = Callable[[Settings], LlmClient]

#: Wall clock for generating fixes: 6 vision calls and a few text calls.
FIXES_TIMEOUT_S: Final[float] = 180.0
#: Wall clock for building the patch (fetching assets, encoding images).
BUILD_TIMEOUT_S: Final[float] = 120.0
MANIFEST: Final[str] = "manifest.json"

mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/avif", ".avif")
mimetypes.add_type("font/woff2", ".woff2")


def _errors(*codes: int) -> dict[int | str, dict[str, Any]]:
    return {code: {"model": ApiErrorEnvelope} for code in codes}


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


class PatchRunner:
    """Runs fix generation and patch jobs; at most one per scan at a time."""

    def __init__(
        self,
        *,
        settings: Settings,
        repository: ScanRepository,
        runner: ScanRunner,
        llm_factory: LlmFactory,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.runner = runner
        self.llm_factory = llm_factory
        self._busy: set[str] = set()
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def is_busy(self, scan_id: str) -> bool:
        return scan_id in self._busy

    def ensure_idle(self, scan_id: str) -> None:
        """Refuse a second fix or patch job for a scan that already has one."""
        if scan_id in self._busy:
            raise ApiException(
                ErrorCode.INVALID_REQUEST,
                "fixes are already being generated or applied for this scan; wait for it to finish",
            )

    def _claim(self, scan_id: str) -> None:
        self.ensure_idle(scan_id)
        self._busy.add(scan_id)

    def fetcher(self) -> SafeFetcher:
        return SafeFetcher(
            allowed_local_hosts=self.settings.allowed_local_hosts,
            max_total_bytes=self.settings.max_page_bytes,
        )

    def patched_url(self, scan_id: str) -> str:
        return f"{self.settings.patched_base_url}/{scan_id}/index.html"

    # -- fixes (synchronous) ---------------------------------------------- #

    async def generate(self, scan: Scan, log: ScanEventLog) -> FixesResponse:
        self._claim(scan.id)
        started = time.monotonic()
        ended = False  # a terminal event (done or error) has been published
        try:
            await log.publish("step", StepEvent(name="fixes", status=StepStatus.RUNNING))
            try:
                async with asyncio.timeout(FIXES_TIMEOUT_S):
                    fetcher = self.fetcher()
                    source = await fetch_source(scan.url, fetcher)
                    client = self.llm_factory(self.settings)
                    plan = await generate_plan(scan, source, client, fetcher)
            except FetchError as exc:
                code = ErrorCode.URL_BLOCKED if exc.blocked else ErrorCode.NAV_FAILED
                raise ApiException(code, f"could not fetch the page source: {exc}") from exc
            except TimeoutError as exc:
                raise ApiException(
                    ErrorCode.TIMEOUT, f"fix generation took longer than {FIXES_TIMEOUT_S:.0f} s"
                ) from exc
            except ApiException:
                raise
            except Exception as exc:
                # A bug must fail this phase cleanly, never leave its stream open.
                logger.exception("fix generation for %s failed unexpectedly", scan.id)
                raise ApiException(
                    ErrorCode.PATCH_FAILED, "fix generation failed; see the server log"
                ) from exc
            work = self.settings.work_dir / scan.id
            await asyncio.to_thread(save_plan, work, plan, source.html)
            fixes = [p.fix for p in plan.fixes]
            # New fixes replace any earlier patch: its zip and preview no longer match.
            await self.repository.save_patch(
                scan.id, PatchInfo(fixes=fixes, ai_usage=plan.ai_usage)
            )
            usage = plan.ai_usage
            await log.publish(
                "step",
                StepEvent(
                    name="fixes",
                    status=StepStatus.OK,
                    ms=_ms(started),
                    detail=(
                        f"{sum(not f.manual_review for f in fixes)} automatic, "
                        f"{sum(f.manual_review for f in fixes)} manual; AI: "
                        f"{usage.live_calls} live, {usage.cached_calls} cached call(s)"
                        + (f" ({usage.unavailable_reason})" if usage.unavailable_reason else "")
                    ),
                ),
            )
            await log.publish("done", DoneEvent(scan_id=scan.id, status=ScanStatus.DONE))
            ended = True
            return FixesResponse(scan_id=scan.id, fixes=fixes, ai_usage=usage)
        except ApiException as exc:
            await self._fail_fixes(log, started, ApiError(code=exc.code, message=exc.message))
            ended = True
            raise
        except OSError as exc:  # saving the plan or the patch row
            logger.exception("could not store fixes for %s", scan.id)
            await self._fail_fixes(
                log,
                started,
                ApiError(code=ErrorCode.PATCH_FAILED, message=f"could not store fixes: {exc}"),
            )
            ended = True
            raise ApiException(ErrorCode.PATCH_FAILED, f"could not store fixes: {exc}") from exc
        finally:
            self._busy.discard(scan.id)
            if not ended:
                # Cancelled (the client went away) or failed after a partial
                # write: the phase still ends with exactly one terminal event.
                with contextlib.suppress(RuntimeError):
                    await self._fail_fixes(
                        log,
                        started,
                        ApiError(
                            code=ErrorCode.CANCELLED, message="fix generation was interrupted"
                        ),
                    )
            await log.close()

    @staticmethod
    async def _fail_fixes(log: ScanEventLog, started: float, error: ApiError) -> None:
        await log.publish(
            "step",
            StepEvent(name="fixes", status=StepStatus.ERROR, ms=_ms(started), detail=error.message),
        )
        await log.publish("error", error)

    # -- patch + rescan (background) -------------------------------------- #

    async def submit(self, scan: Scan, accepted: list[str], log: ScanEventLog) -> None:
        self._claim(scan.id)
        task = asyncio.create_task(self._run(scan, accepted, log), name=f"patch-{scan.id}")
        self._tasks[scan.id] = task
        task.add_done_callback(lambda _: self._tasks.pop(scan.id, None))

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=15)

    async def _run(self, scan: Scan, accepted: list[str], log: ScanEventLog) -> None:
        error: ApiError | None = None
        step = "patch"
        started = time.monotonic()
        try:
            await log.publish("step", StepEvent(name="patch", status=StepStatus.RUNNING))
            loaded = await asyncio.to_thread(load_plan, self.settings.work_dir / scan.id)
            if loaded is None:
                raise ScanFailed(ErrorCode.PATCH_FAILED.value, "generate fixes first")
            plan, source_html = loaded
            async with asyncio.timeout(BUILD_TIMEOUT_S):
                built = await build_patch(
                    plan,
                    source_html,
                    accepted,
                    out_dir=self.settings.patched_dir / scan.id,
                    zip_path=self.settings.zip_dir / f"{scan.id}.zip",
                    fetcher=self.fetcher(),
                    patched_url=self.patched_url(scan.id),
                )
            manifest = self.settings.work_dir / scan.id / MANIFEST
            await asyncio.to_thread(
                manifest.write_text, json.dumps({"csp": built.csp}, indent=2), "utf-8"
            )
            patch = PatchInfo(
                fixes=built.fixes,
                zip_path=str(built.zip_path),
                patched_url=self.patched_url(scan.id),
                skipped=built.skipped,
                ai_usage=plan.ai_usage,
            )
            await self.repository.save_patch(scan.id, patch)
            await log.publish(
                "step",
                StepEvent(
                    name="patch",
                    status=StepStatus.OK,
                    ms=_ms(started),
                    detail=f"{built.applied_count} applied, {len(built.skipped)} skipped",
                ),
            )

            step = "rescan"
            started = time.monotonic()
            await log.publish("step", StepEvent(name="rescan", status=StepStatus.RUNNING))
            weights = scan.before.scores.weights if scan.before else Weights()
            async with self.runner.slots:
                async with asyncio.timeout(self.settings.scan_timeout_s):
                    after = await self._rescan(scan, weights, log)
            if scan.before is not None:
                after = carry_hosting(after, scan.before.green, weights)
            await self.repository.save_after(scan.id, after, patch)
            await log.publish(
                "step",
                StepEvent(
                    name="rescan",
                    status=StepStatus.OK,
                    ms=_ms(started),
                    detail=(
                        f"a11y {after.scores.a11y}, carbon {after.scores.carbon} "
                        f"({after.scores.carbon_grade.value}), combined {after.scores.combined}"
                    ),
                ),
            )
            await log.publish("done", DoneEvent(scan_id=scan.id, status=ScanStatus.DONE))
        except asyncio.CancelledError:
            error = ApiError(code=ErrorCode.CANCELLED, message="the patch job was cancelled")
        except TimeoutError:
            limit = BUILD_TIMEOUT_S if step == "patch" else self.settings.scan_timeout_s
            error = ApiError(
                code=ErrorCode.TIMEOUT, message=f"{step} did not finish within {limit:.0f} s"
            )
        except ScanFailed as failure:
            code = ErrorCode.PATCH_FAILED if step == "patch" else _code(failure.code)
            error = ApiError(code=code, message=str(failure))
        except (OSError, ValueError) as exc:
            logger.exception("patch job for %s failed", scan.id)
            error = ApiError(code=ErrorCode.PATCH_FAILED, message=f"{step} failed: {exc}")
        except Exception:
            # Task boundary: a bug must end the stream, never hang it.
            logger.exception("patch job for %s failed unexpectedly", scan.id)
            error = ApiError(
                code=ErrorCode.PATCH_FAILED, message=f"{step} failed; see the server log"
            )
        finally:
            self._busy.discard(scan.id)
            try:
                if error is not None:
                    with contextlib.suppress(RuntimeError):
                        await log.publish(
                            "step",
                            StepEvent(
                                name=step,
                                status=StepStatus.ERROR,
                                ms=_ms(started),
                                detail=error.message,
                            ),
                        )
                        await log.publish("error", error)
            finally:
                await log.close()

    async def _rescan(self, scan: Scan, weights: Weights, log: ScanEventLog) -> Any:
        job = self.runner.job_factory(
            JobSpec(
                scan_id=scan.id,
                url=self.patched_url(scan.id),
                weights=weights,
                settings=self.settings,
                screenshot_dir=self.settings.screenshot_dir / scan.id / "after",
            )
        )
        async with contextlib.aclosing(job.run()) as steps:
            async for inner in steps:
                if inner.status is StepStatus.ERROR:
                    continue  # the failure is raised and reported once, below
                await log.publish(
                    "step",
                    StepEvent(
                        name="rescan",
                        status=StepStatus.RUNNING,
                        detail=f"{inner.name}: {inner.status.value}"
                        + (f" ({inner.detail})" if inner.detail else ""),
                    ),
                )
        if job.result is None:
            raise ScanFailed(ErrorCode.NAV_FAILED.value, "the re-scan produced no result")
        return job.result


def _code(raw: str) -> ErrorCode:
    try:
        return ErrorCode(raw)
    except ValueError:
        return ErrorCode.NAV_FAILED


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


async def _finished_scan(state: AppState, scan_id: str) -> Scan:
    scan = await state.repository.get(scan_id)
    if scan is None:
        raise ApiException(ErrorCode.NOT_FOUND, f"no scan with id {scan_id!r}")
    if scan.status is not ScanStatus.DONE or scan.before is None:
        raise ApiException(
            ErrorCode.INVALID_REQUEST, "fixes need a finished scan; wait for its done event"
        )
    return scan


@router.post(
    "/scans/{scan_id}/fixes",
    response_model=FixesResponse,
    responses=_errors(400, 404, 422, 502, 504),
    summary="Generate fixes for a finished scan",
)
async def generate_fixes(scan_id: str, state: State) -> FixesResponse:
    """Every fix, automatic and manual, with the AI usage behind them.

    AI-assisted values are labelled ``ai_generated`` ("AI-generated, review
    before use"). When the LLM is unavailable (``LLM_OFFLINE`` with no cached
    answer, or no key), those fixes fall back to page-context values or are
    marked ``manual_review``, and ``ai_usage.unavailable_reason`` says why.
    """
    scan = await _finished_scan(state, scan_id)
    state.patches.ensure_idle(scan_id)
    log = await state.hub.reopen(scan_id)
    return await state.patches.generate(scan, log)


@router.post(
    "/scans/{scan_id}/patch",
    status_code=202,
    response_model=PatchAccepted,
    responses=_errors(404, 422),
    summary="Apply accepted fixes, serve the patched copy and re-scan it",
)
async def apply_patch(scan_id: str, body: PatchRequest, state: State) -> PatchAccepted:
    scan = await _finished_scan(state, scan_id)
    if scan.patch is None:
        raise ApiException(ErrorCode.INVALID_REQUEST, "generate fixes first")
    known = {fix.id for fix in scan.patch.fixes}
    unknown = sorted(set(body.accepted_fix_ids) - known)
    if unknown:
        raise ApiException(
            ErrorCode.INVALID_REQUEST, f"unknown fix id(s): {', '.join(unknown[:5])}"
        )
    state.patches.ensure_idle(scan_id)
    log = await state.hub.reopen(scan_id)
    events_after = log.last_id
    await state.patches.submit(scan, body.accepted_fix_ids, log)
    return PatchAccepted(scan_id=scan_id, events_after=events_after)


@router.get(
    "/scans/{scan_id}/patch.zip",
    response_class=FileResponse,
    responses={200: {"content": {"application/zip": {}}}, **_errors(404)},
    summary="Download the patched site",
)
async def patch_zip(scan_id: str, state: State) -> FileResponse:
    scan = await state.repository.get(scan_id)
    if scan is None or scan.patch is None or not scan.patch.zip_path:
        raise ApiException(ErrorCode.NOT_FOUND, f"no patch has been built for scan {scan_id!r}")
    path = await asyncio.to_thread(_servable_zip, state.settings.zip_dir, scan.patch.zip_path)
    if path is None:
        raise ApiException(ErrorCode.NOT_FOUND, f"no patch has been built for scan {scan_id!r}")
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"greenaccess-patch-{scan_id[:12]}.zip",
    )


def _servable_zip(zip_dir: Path, raw: str) -> Path | None:
    """The stored zip, only if it is a file inside the zip folder. Blocking."""
    root = zip_dir.resolve()
    path = Path(raw).resolve()
    return path if path.is_relative_to(root) and path.is_file() else None


def _servable(root: Path, scan_id: str, rel: str) -> Path | None:
    """A regular file under ``patched/{scan_id}/``, or None. Blocking; run in a thread."""
    if not scan_id.isalnum():
        return None
    base = (root / scan_id).resolve()
    path = (base / (rel or "index.html")).resolve()
    if path.is_dir():
        path = path / "index.html"
    if not path.is_relative_to(base) or not path.is_file():
        return None
    return path


def _csp(work_dir: Path, scan_id: str) -> str:
    try:
        return str(json.loads((work_dir / scan_id / MANIFEST).read_text("utf-8"))["csp"])
    except (OSError, ValueError, KeyError):
        # No manifest yet: the strictest policy, never a missing one.
        return "default-src 'self' data:; script-src 'self'; connect-src 'none'; base-uri 'none'"


@patched_router.get("/patched/{scan_id}/{rel:path}", include_in_schema=False)
async def patched_file(scan_id: str, rel: str, state: State) -> Response:
    """Static files only, with the preview CSP (MASTERSPEC §8.3). Nothing runs here."""
    path = await asyncio.to_thread(_servable, state.settings.patched_dir, scan_id, rel)
    if path is None:
        raise ApiException(ErrorCode.NOT_FOUND, "no such patched file")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        headers={
            "Content-Security-Policy": await asyncio.to_thread(
                _csp, state.settings.work_dir, scan_id
            ),
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
        },
    )
