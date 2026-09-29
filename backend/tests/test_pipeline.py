"""Scan pipeline and CLI tests (MASTERSPEC §4, §12).

The event sequence is what the SSE endpoint will stream later, so its order and
statuses are pinned here.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from app import cli
from app.carbon import swd
from app.config import Settings, get_settings
from app.models import ErrorCode, GreenResult, GreenSource, StepStatus
from app.scanner.pipeline import SCAN_STEPS, ScanError, ScanPipeline, _scan_slots

HTML = {"Content-Type": "text/html; charset=utf-8"}

PAGE = b"""<!doctype html>
<html><head><title>Tiny</title><style>.a { transition: color 200ms; }</style></head>
<body><main><h1>Tiny page</h1>
<img src="/wide.png" alt="">
<button onclick="x()">Go</button>
<span class="fake" onclick="x()">Fake button</span>
</main></body></html>
"""


def _png() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (1200, 300), (20, 90, 50)).save(buffer, format="PNG")
    return buffer.getvalue()


def _settings(base: str, executable: str | None) -> Settings:
    return Settings(
        allowed_local_hosts=(base.removeprefix("http://"),),
        chromium_executable=executable,
        scan_timeout_s=60,
        nav_timeout_s=20,
    )


async def _never_called(host: str) -> GreenResult:
    raise AssertionError(f"green lookup must be skipped for local host {host}")


# --------------------------------------------------------------------------- #
# Blocked before any browser starts
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8081/",
        "file:///etc/passwd",
        "http://[::ffff:169.254.169.254]/",
    ],
)
async def test_blocked_url_stops_at_validate(url: str):
    pipeline = ScanPipeline(url, settings=Settings(), green_checker=_never_called)
    events = []
    with pytest.raises(ScanError) as excinfo:
        async for event in pipeline.events():
            events.append(event)

    assert excinfo.value.code is ErrorCode.URL_BLOCKED
    assert excinfo.value.step == "validate"
    assert [(e.name, e.status) for e in events] == [
        ("validate", StepStatus.RUNNING),
        ("validate", StepStatus.ERROR),
    ]
    assert pipeline.result is None


# --------------------------------------------------------------------------- #
# A full scan of a tiny page
# --------------------------------------------------------------------------- #


@pytest.mark.browser
async def test_full_scan_emits_every_step_in_order(
    local_site: Callable, chromium_executable, tmp_path: Path
):
    base = local_site(
        {"/": (200, HTML, PAGE), "/wide.png": (200, {"Content-Type": "image/png"}, _png())}
    )
    pipeline = ScanPipeline(
        base + "/",
        settings=_settings(base, chromium_executable),
        screenshot_dir=tmp_path,
        green_checker=_never_called,
    )
    events = [event async for event in pipeline.events()]

    finished = [(e.name, e.status) for e in events if e.status is not StepStatus.RUNNING]
    assert [name for name, _ in finished] == list(SCAN_STEPS)
    assert all(status is StepStatus.OK for name, status in finished[:7])
    assert all(status is StepStatus.SKIPPED for _, status in finished[7:])
    # Every real step announced itself before finishing.
    running = [e.name for e in events if e.status is StepStatus.RUNNING]
    assert running == list(SCAN_STEPS[:7])

    result = pipeline.result
    assert result is not None
    assert result.scores.is_placeholder is True
    assert result.tradeoffs == []
    assert result.green.source is GreenSource.UNAVAILABLE
    assert await asyncio.to_thread(Path(result.screenshot_path).is_file)
    assert result.engine_versions.axe == "4.13.0"
    assert result.aria_snapshot

    carbon = result.carbon
    assert carbon.request_count >= 2
    assert carbon.grams_per_view == pytest.approx(swd.per_visit(carbon.total_bytes))
    names = {d.detector for d in carbon.detections}
    assert {"no_dimensions", "no_reduced_motion", "div_soup_widgets"} <= names
    soup = next(d for d in carbon.detections if d.detector == "div_soup_widgets")
    assert len(soup.evidence) == 1 and "span.fake" in soup.evidence[0], "the real <button> is fine"
    # The page has alt="" and a main landmark: no image-alt violation.
    assert "image-alt" not in {v.rule_id for v in result.a11y.violations}


@pytest.mark.browser
async def test_navigation_failure_is_reported(local_site: Callable, chromium_executable):
    base = local_site({})
    host, port = base.removeprefix("http://").split(":")
    # An allow-listed port with nothing listening on it.
    dead = f"http://{host}:{int(port) + 1 if int(port) < 65535 else 1}/"
    settings = replace(
        _settings(base, chromium_executable),
        allowed_local_hosts=(dead.removeprefix("http://").rstrip("/"),),
    )
    pipeline = ScanPipeline(
        dead, settings=settings, green_checker=_never_called, screenshot_dir=None
    )
    with pytest.raises(ScanError) as excinfo:
        await pipeline.run()
    assert excinfo.value.code in {ErrorCode.NAV_FAILED, ErrorCode.TIMEOUT}
    assert excinfo.value.step == "load"


# --------------------------------------------------------------------------- #
# Green hosting and limits (no browser)
# --------------------------------------------------------------------------- #


async def test_green_host_rebuilds_carbon_with_renewable_intensity():
    from app.carbon.detectors import DetectorReport
    from app.carbon.report import build_carbon_result
    from app.scanner.network import NetworkSummary
    from app.security.ssrf import ValidatedUrl

    async def green(host: str) -> GreenResult:
        return GreenResult(host=host, green=True, hosted_by="Leafy", source=GreenSource.GREENWEB)

    pipeline = ScanPipeline("https://example.org/", settings=Settings(), green_checker=green)
    pipeline._validated = ValidatedUrl(
        url="https://example.org/",
        scheme="https",
        host="example.org",
        port=443,
        resolved_ips=("93.184.216.34",),
    )
    pipeline._summary = NetworkSummary(total_bytes=2_000_000, request_count=10)
    pipeline._report = DetectorReport()
    pipeline._carbon = build_carbon_result(pipeline._summary, pipeline._report, green=False)
    grey = pipeline._carbon.grams_per_view

    detail = await pipeline._run_green()

    assert detail == "green host"
    assert pipeline._carbon.assumptions.green_hosted is True
    assert pipeline._carbon.grams_per_view == pytest.approx(swd.per_visit(2_000_000, green=True))
    assert pipeline._carbon.grams_per_view < grey


async def test_green_lookup_failure_is_unknown_not_an_error():
    from app.security.ssrf import ValidatedUrl

    async def down(host: str) -> GreenResult:
        return GreenResult(host=host, source=GreenSource.UNAVAILABLE)

    pipeline = ScanPipeline("https://example.org/", settings=Settings(), green_checker=down)
    pipeline._validated = ValidatedUrl(
        url="https://example.org/",
        scheme="https",
        host="example.org",
        port=443,
        resolved_ips=("93.184.216.34",),
    )
    detail = await pipeline._run_green()
    assert "unavailable" in detail
    assert pipeline._green is not None and pipeline._green.green is False


async def test_deadline_turns_into_a_timeout_error():
    pipeline = ScanPipeline("https://example.org/", settings=Settings(scan_timeout_s=1))
    pipeline._deadline = time.monotonic() + 0.05
    with pytest.raises(ScanError) as excinfo:
        await pipeline._within_deadline("a11y", asyncio.sleep(5))
    assert excinfo.value.code is ErrorCode.TIMEOUT


async def test_scan_slots_are_shared_within_a_loop_and_bounded():
    first = _scan_slots(2)
    assert _scan_slots(2) is first
    await first.acquire()
    await first.acquire()
    assert first.locked(), "a third scan must wait (MAX_CONCURRENT_SCANS=2)"
    first.release()
    first.release()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_cli_blocked_url_prints_the_error_envelope(capsys: pytest.CaptureFixture[str]):
    code = cli.main(["scan", "http://169.254.169.254"])
    out = capsys.readouterr()
    assert code == cli.EXIT_BLOCKED
    envelope = json.loads(out.out)
    assert envelope["error"]["code"] == "URL_BLOCKED"
    assert "metadata" in envelope["error"]["message"]


@pytest.mark.browser
def test_cli_writes_scan_json(
    local_site: Callable,
    chromium_executable,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    base = local_site(
        {"/": (200, HTML, PAGE), "/wide.png": (200, {"Content-Type": "image/png"}, _png())}
    )
    monkeypatch.setenv("ALLOWED_LOCAL_HOSTS", base.removeprefix("http://"))
    if chromium_executable:
        monkeypatch.setenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE", chromium_executable)
    monkeypatch.setattr("app.scanner.pipeline.DEFAULT_SCREENSHOT_DIR", tmp_path)
    get_settings.cache_clear()
    out_file = tmp_path / "scan.json"
    try:
        code = cli.main(["scan", base + "/", "--out", str(out_file)])
    finally:
        get_settings.cache_clear()

    captured = capsys.readouterr()
    assert code == cli.EXIT_OK
    assert "automated checks" in captured.err
    assert "estimate" in captured.err
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["scores"]["is_placeholder"] is True
    assert data["carbon"]["is_estimate"] is True
    assert data["carbon"]["total_bytes"] > 0
