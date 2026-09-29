"""Slow end-to-end test: scan the Daily Herald through the HTTP API (Phase 2 gate).

Real pipeline, real Chromium, real demo servers on :8081/:8082: POST the demo
URL, read the SSE stream to completion, then read the persisted scan and its
screenshot back. Marked ``integration`` and ``browser``; skipped when Chromium
or the demo assets are missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models import Scan, ScanStatus, TradeoffType
from tests.api_support import ALL_STEPS, make_settings, read_events
from tests.test_demo_integration import DEMO_URL, _chromium_available, demo_servers  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.browser]


@pytest.fixture(scope="module")
def chromium() -> None:
    # Sync fixture: the check uses Playwright's sync API, which cannot run
    # inside the async test's event loop.
    if not _chromium_available():
        pytest.skip("chromium is not installed")


async def test_demo_scan_through_the_api(
    tmp_path: Path,
    chromium: None,
    demo_servers: None,  # noqa: F811
) -> None:
    import httpx

    from app.main import create_app

    settings = make_settings(
        tmp_path,
        allowed_local_hosts=("localhost:8081", "localhost:8082"),
        scan_timeout_s=90,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as client:
            created = await client.post("/api/scans", json={"url": DEMO_URL})
            assert created.status_code == 202, created.text
            scan_id = created.json()["scan_id"]

            events = await read_events(client, scan_id)
            scan = Scan.model_validate((await client.get(f"/api/scans/{scan_id}")).json())
            screenshot = await client.get(f"/api/scans/{scan_id}/screenshot")

    # The stream: every step, in order, running then ok, then done.
    assert events[-1].event == "done", events[-1]
    steps = [(e.data["name"], e.data["status"]) for e in events if e.event == "step"]
    assert steps == [(name, status) for name in ALL_STEPS for status in ("running", "ok")]

    # The persisted scan carries real, computed scores and the trade-offs.
    assert scan.status is ScanStatus.DONE
    assert scan.before is not None
    assert not scan.before.scores.is_placeholder
    assert scan.before.carbon.total_bytes > 2_000_000
    assert len(scan.before.tradeoffs) >= 6
    assert any(f.type is TradeoffType.TENSION for f in scan.before.tradeoffs)

    assert screenshot.status_code == 200
    assert screenshot.headers["content-type"] == "image/png"
    assert screenshot.content.startswith(b"\x89PNG")
