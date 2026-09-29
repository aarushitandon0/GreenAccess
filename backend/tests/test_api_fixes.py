"""API tests for the fix pipeline (MASTERSPEC §12), with a stubbed scan pipeline.

The page source is served by a real local HTTP server, so fix generation and
the patch build run for real; only the Playwright scan (before and re-scan)
is a stub, whose numbers are test data.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from app.models import (
    CarbonResult,
    Detection,
    EngineVersions,
    FixesResponse,
    GreenResult,
    ScanResult,
    Scores,
)
from tests.api_support import (
    ALLOWED,
    StubControl,
    make_settings,
    parse_sse,
    read_events,
    running_app,
)

PAGE = b"""<!doctype html><html><head><title>Test page</title>
<link rel="stylesheet" href="/s.css"></head>
<body><h1>Hello</h1><p>The quick brown fox jumps over the lazy dog and the cat is on the mat
with the other animals that are in the garden for the day.</p></body></html>"""
CSS = b"@keyframes spin { to { transform: rotate(1turn) } } h1 { animation: spin 2s infinite }"


def result_with_motion(host: str) -> ScanResult:
    return ScanResult(
        scores=Scores(a11y=90, carbon=80, combined=85, is_placeholder=False),
        carbon=CarbonResult(
            total_bytes=1000,
            detections=[Detection(detector="no_reduced_motion", summary="animations")],
        ),
        green=GreenResult(host=host),
        engine_versions=EngineVersions(playwright="test", axe="test", swd_model="3"),
    )


def settings_for(tmp_path: Path):
    return make_settings(tmp_path, allowed_local_hosts=(*ALLOWED, "127.0.0.1"))


async def _finished_scan(client, url: str) -> str:
    response = await client.post("/api/scans", json={"url": url})
    assert response.status_code == 202, response.text
    scan_id = response.json()["scan_id"]
    events = await read_events(client, scan_id)
    assert events[-1].event == "done"
    return scan_id


def site(local_site) -> str:
    return local_site(
        {
            "/": (200, {"Content-Type": "text/html; charset=utf-8"}, PAGE),
            "/s.css": (200, {"Content-Type": "text/css"}, CSS),
        }
    )


async def test_generate_fixes_reports_fixes_usage_and_sse(tmp_path: Path, local_site) -> None:
    base = site(local_site)
    control = StubControl(result=result_with_motion("127.0.0.1"))
    async with running_app(settings_for(tmp_path), control=control) as (client, _):
        scan_id = await _finished_scan(client, f"{base}/")
        response = await client.post(f"/api/scans/{scan_id}/fixes")
        assert response.status_code == 200, response.text
        body = FixesResponse.model_validate(response.json())
        events = await read_events(client, scan_id)
        stored = (await client.get(f"/api/scans/{scan_id}")).json()

    kinds = {f.kind for f in body.fixes}
    assert "reduced_motion" in kinds
    # Nothing on this page needed the LLM, so nothing is reported unavailable.
    assert body.ai_usage.offline is True and body.ai_usage.unavailable_reason is None
    assert body.ai_usage.live_calls == 0 and body.ai_usage.cached_calls == 0
    names = [(e.event, e.data.get("name")) for e in events]
    assert ("step", "fixes") in names
    assert events[-1].event == "done"
    assert len([e for e in events if e.event == "done"]) == 2  # scan phase + fixes phase
    assert stored["patch"]["fixes"] and stored["patch"]["ai_usage"]["offline"] is True


async def test_patch_builds_serves_rescans_and_zips(tmp_path: Path, local_site) -> None:
    base = site(local_site)
    control = StubControl(result=result_with_motion("127.0.0.1"))
    async with running_app(settings_for(tmp_path), control=control) as (client, _):
        scan_id = await _finished_scan(client, f"{base}/")
        fixes = (await client.post(f"/api/scans/{scan_id}/fixes")).json()["fixes"]
        accepted = [f["id"] for f in fixes if not f["manual_review"]]
        started = await client.post(
            f"/api/scans/{scan_id}/patch", json={"accepted_fix_ids": accepted}
        )
        assert started.status_code == 202, started.text
        after_id = started.json()["events_after"]
        events = parse_sse(
            (await client.get(f"/api/scans/{scan_id}/events", params={"after": after_id})).text
        )
        scan = (await client.get(f"/api/scans/{scan_id}")).json()
        page = await client.get(f"/patched/{scan_id}/index.html")
        css = await client.get(f"/patched/{scan_id}/greenaccess-patch.css")
        copied = await client.get(f"/patched/{scan_id}/assets/s.css")
        archive = await client.get(f"/api/scans/{scan_id}/patch.zip")
        escape = await client.get(f"/patched/{scan_id}/..%2F..%2Fwork/{scan_id}/plan.json")

    assert all(e.id > after_id for e in events)
    steps = [e.data["name"] for e in events if e.event == "step"]
    assert steps[0] == "patch" and "rescan" in steps
    assert events[-1].event == "done", events[-1]
    assert control.specs[-1].url.endswith(f"/patched/{scan_id}/index.html")  # same job factory

    assert scan["after"] is not None
    assert scan["patch"]["patched_url"].endswith(f"/patched/{scan_id}/index.html")
    applied = [f for f in scan["patch"]["fixes"] if f["applied"]]
    assert {f["kind"] for f in applied} == {"reduced_motion"}

    assert page.status_code == 200
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "connect-src 'none'" in csp
    assert page.headers["x-content-type-options"] == "nosniff"
    assert 'href="assets/s.css"' in page.text and "greenaccess-patch.css" in page.text
    assert "prefers-reduced-motion" in css.text
    assert copied.status_code == 200 and b"keyframes" in copied.content
    assert escape.status_code == 404

    assert archive.status_code == 200
    assert archive.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        assert {"CHANGES.md", "index.html", "greenaccess-patch.css"} <= set(bundle.namelist())


async def test_fixes_need_a_finished_scan(tmp_path: Path) -> None:
    import asyncio

    control = StubControl(gate=asyncio.Event())
    async with running_app(settings_for(tmp_path), control=control) as (client, _):
        scan_id = (await client.post("/api/scans", json={"url": "http://demo.test/"})).json()[
            "scan_id"
        ]
        response = await client.post(f"/api/scans/{scan_id}/fixes")
        control.gate.set()
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


async def test_patch_needs_generated_fixes_and_known_ids(tmp_path: Path, local_site) -> None:
    base = site(local_site)
    async with running_app(settings_for(tmp_path)) as (client, _):
        scan_id = await _finished_scan(client, f"{base}/")
        early = await client.post(f"/api/scans/{scan_id}/patch", json={"accepted_fix_ids": []})
        await client.post(f"/api/scans/{scan_id}/fixes")
        unknown = await client.post(
            f"/api/scans/{scan_id}/patch", json={"accepted_fix_ids": ["made-up"]}
        )
    assert early.status_code == 422 and "generate fixes first" in early.json()["error"]["message"]
    assert unknown.status_code == 422 and "made-up" in unknown.json()["error"]["message"]


async def test_unknown_scan_and_missing_zip_are_not_found(tmp_path: Path, local_site) -> None:
    base = site(local_site)
    async with running_app(settings_for(tmp_path)) as (client, _):
        missing = await client.post("/api/scans/nope/fixes")
        scan_id = await _finished_scan(client, f"{base}/")
        no_zip = await client.get(f"/api/scans/{scan_id}/patch.zip")
        no_file = await client.get(f"/patched/{scan_id}/index.html")
    assert missing.status_code == 404
    assert no_zip.status_code == 404
    assert no_file.status_code == 404


async def test_unreachable_source_is_reported_on_the_stream(tmp_path: Path) -> None:
    """The stub scan 'finished', but the page itself cannot be fetched."""
    async with running_app(settings_for(tmp_path)) as (client, _):
        scan_id = await _finished_scan(client, "http://127.0.0.1:9/")
        response = await client.post(f"/api/scans/{scan_id}/fixes")
        events = await read_events(client, scan_id)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "NAV_FAILED"
    assert events[-1].event == "error"


async def test_openapi_documents_the_fix_routes(tmp_path: Path) -> None:
    async with running_app(settings_for(tmp_path)) as (client, _):
        schema = (await client.get("/openapi.json")).json()
    paths = schema["paths"]
    assert "/api/scans/{scan_id}/fixes" in paths
    assert "/api/scans/{scan_id}/patch" in paths
    assert "/api/scans/{scan_id}/patch.zip" in paths
    assert not any(p.startswith("/patched") for p in paths)


async def test_a_bug_in_generation_still_ends_the_stream(
    tmp_path: Path, local_site, monkeypatch
) -> None:
    import app.api.fixes as fixes_module

    async def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(fixes_module, "generate_plan", broken)
    base = site(local_site)
    async with running_app(settings_for(tmp_path)) as (client, _):
        scan_id = await _finished_scan(client, f"{base}/")
        response = await client.post(f"/api/scans/{scan_id}/fixes")
        events = await read_events(client, scan_id)
        retry = await client.post(f"/api/scans/{scan_id}/fixes")  # not stuck "busy"
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "PATCH_FAILED"
    assert "boom" not in response.json()["error"]["message"]  # internals stay in the log
    assert events[-1].event == "error"
    assert retry.status_code == 500
