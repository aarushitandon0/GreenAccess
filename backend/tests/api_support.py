"""Shared scaffolding for the API tests.

A stub scan job stands in for the Playwright pipeline so the API can be tested
in milliseconds; the real pipeline is exercised by the marked integration test.
Nothing here is a fixture for real results: every number the stub reports is
test data and is only ever asserted on as such.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI

from app.api.ratelimit import SlidingWindowLimiter
from app.api.runner import JobSpec, ScanJob
from app.config import Settings
from app.main import create_app
from app.models import (
    CarbonGrade,
    EngineVersions,
    GreenResult,
    ScanResult,
    Scores,
    StepEvent,
    StepStatus,
)
from app.security.ssrf import ValidatedUrl, validate_url

#: The scan steps the real pipeline emits, in MASTERSPEC §12 order. The runner
#: adds `persist` itself.
PIPELINE_STEPS: tuple[str, ...] = (
    "validate",
    "load",
    "a11y",
    "keyboard",
    "aria",
    "carbon",
    "green",
    "score",
    "tradeoffs",
)
ALL_STEPS: tuple[str, ...] = (*PIPELINE_STEPS, "persist")

#: Smallest valid PNG: a 1x1 transparent pixel.
PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)

ALLOWED = ("demo.test", "other.test", "localhost")


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": f"sqlite:///{tmp_path / 'db' / 'test.db'}",
        "data_dir": tmp_path / "data",
        "allowed_local_hosts": ALLOWED,
        "max_concurrent_scans": 2,
        "scan_timeout_s": 30,
        "demo_url": "http://localhost:8081",
    }
    values.update(overrides)
    return Settings(**values)


async def offline_preflight(url: str, allowed: tuple[str, ...]) -> ValidatedUrl:
    """The SSRF check without the network: validates the URL, follows nothing."""
    return await asyncio.to_thread(validate_url, url, allowed_local_hosts=allowed)


def stub_result(host: str, *, screenshot_path: str = "", combined: int = 39) -> ScanResult:
    return ScanResult(
        scores=Scores(
            a11y=40, carbon=38, combined=combined, carbon_grade=CarbonGrade.E, is_placeholder=False
        ),
        green=GreenResult(host=host),
        screenshot_path=screenshot_path,
        engine_versions=EngineVersions(playwright="test", axe="test", swd_model="3"),
    )


@dataclass
class StubControl:
    """Knobs and observations shared by every stub job an app creates."""

    #: If set, each job waits on it after emitting `load: running`.
    gate: asyncio.Event | None = None
    #: Raised by the job after its steps, instead of producing a result.
    fail: BaseException | None = None
    #: Never finish (for timeout and cancel tests).
    hang: bool = False
    write_screenshot: bool = False
    screenshot_override: str | None = None
    combined: int = 39
    active: int = 0
    max_active: int = 0
    started: list[str] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)
    specs: list[JobSpec] = field(default_factory=list)


class StubJob:
    def __init__(self, spec: JobSpec, control: StubControl) -> None:
        self.spec = spec
        self.control = control
        self.result: ScanResult | None = None

    async def run(self) -> AsyncIterator[StepEvent]:
        control = self.control
        control.started.append(self.spec.scan_id)
        control.active += 1
        control.max_active = max(control.max_active, control.active)
        try:
            for name in PIPELINE_STEPS:
                yield StepEvent(name=name, status=StepStatus.RUNNING)
                if name == "load" and control.gate is not None:
                    await control.gate.wait()
                if name == "load" and control.hang:
                    await asyncio.Event().wait()
                yield StepEvent(name=name, status=StepStatus.OK, ms=1)
            if control.fail is not None:
                raise control.fail
            screenshot = ""
            if control.write_screenshot:
                self.spec.screenshot_dir.mkdir(parents=True, exist_ok=True)
                target = self.spec.screenshot_dir / "page.png"
                target.write_bytes(PNG_1X1)
                screenshot = str(target)
            if control.screenshot_override is not None:
                screenshot = control.screenshot_override
            host = httpx.URL(self.spec.url).host
            self.result = stub_result(host, screenshot_path=screenshot, combined=control.combined)
        finally:
            control.active -= 1
            control.closed.append(self.spec.scan_id)


def stub_factory(control: StubControl) -> Callable[[JobSpec], ScanJob]:
    def factory(spec: JobSpec) -> ScanJob:
        control.specs.append(spec)
        return StubJob(spec, control)

    return factory


@asynccontextmanager
async def running_app(
    settings: Settings,
    *,
    control: StubControl | None = None,
    preflight: Any = offline_preflight,
    limiter: SlidingWindowLimiter | None = None,
    client_ip: str = "203.0.113.5",
) -> AsyncIterator[tuple[httpx.AsyncClient, FastAPI]]:
    """An app with its lifespan running and an httpx client bound to it."""
    app = create_app(
        settings,
        job_factory=stub_factory(control or StubControl()),
        preflight=preflight,
        limiter=limiter,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=(client_ip, 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as client:
            yield client, app


@dataclass(frozen=True)
class Sse:
    event: str
    data: dict[str, Any]
    id: int


def parse_sse(body: str) -> list[Sse]:
    """Parse an SSE body. Comments (keep-alive pings) are ignored."""
    events: list[Sse] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.split("\n"):
            if not line or line.startswith(":"):
                continue
            key, _, value = line.partition(":")
            fields[key] = value[1:] if value.startswith(" ") else value
        if "data" in fields:
            events.append(
                Sse(
                    event=fields.get("event", "message"),
                    data=json.loads(fields["data"]),
                    id=int(fields["id"]),
                )
            )
    return events


async def read_events(
    client: httpx.AsyncClient, scan_id: str, headers: dict[str, str] | None = None
) -> list[Sse]:
    response = await asyncio.wait_for(
        client.get(f"/api/scans/{scan_id}/events", headers=headers), timeout=20
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse_sse(response.text)


async def wait_until(predicate: Callable[[], bool], limit_s: float = 5.0) -> None:
    """Poll runner state that has no event of its own to await."""
    async with asyncio.timeout(limit_s):
        while not predicate():  # noqa: ASYNC110 - no event exists for this state
            await asyncio.sleep(0.01)


# --------------------------------------------------------------------------- #
# Real HTTP servers for the redirect pre-flight
# --------------------------------------------------------------------------- #


class RedirectServer:
    """A local server whose every path answers with a fixed redirect."""

    def __init__(self, location: str | Callable[[int], str], status: int = 302) -> None:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                owner.hits.append((self.path, self.headers.get("Host", "")))
                target = owner.location(owner.port) if callable(owner.location) else owner.location
                if target:
                    self.send_response(owner.status)
                    self.send_header("Location", target)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    body = b"<html><body>final</body></html>"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                return

        self.location = location
        self.status = status
        self.hits: list[tuple[str, str]] = []
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> RedirectServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()
