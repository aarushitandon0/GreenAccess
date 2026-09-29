"""API tests for the scan endpoints (MASTERSPEC §12), with a stubbed pipeline.

Covers the Phase 2 gate in-process: ordered SSE steps, URL_BLOCKED for a
private address and for a redirect to one, two scans in parallel with a third
queued, rate limiting, timeouts and failures ending the stream cleanly, replay
for late subscribers, persistence across restarts, and the error envelope.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.api.ratelimit import SlidingWindowLimiter
from app.db.repository import ScanRepository
from app.main import _default_preflight
from app.models import ErrorCode, Scan, ScanStatus
from app.scanner.pipeline import ScanFailed
from tests.api_support import (
    ALL_STEPS,
    PNG_1X1,
    RedirectServer,
    StubControl,
    make_settings,
    read_events,
    running_app,
    wait_until,
)

DEMO = "http://demo.test/"


def assert_envelope(response, status: int, code: ErrorCode) -> dict:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == code.value
    assert body["error"]["message"]
    return body["error"]


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


async def test_post_returns_202_and_a_scan_id(tmp_path: Path) -> None:
    async with running_app(make_settings(tmp_path)) as (client, _):
        response = await client.post("/api/scans", json={"url": DEMO})
    assert response.status_code == 202
    scan_id = response.json()["scan_id"]
    assert len(scan_id) == 32
    assert response.headers["location"] == f"/api/scans/{scan_id}"


async def test_sse_streams_every_step_in_order_then_done(tmp_path: Path) -> None:
    async with running_app(make_settings(tmp_path)) as (client, _):
        scan_id = (await client.post("/api/scans", json={"url": DEMO})).json()["scan_id"]
        events = await read_events(client, scan_id)

    steps = [e for e in events if e.event == "step"]
    # Each step reports running then ok, in MASTERSPEC §12 order.
    expected = [(name, status) for name in ALL_STEPS for status in ("running", "ok")]
    assert [(e.data["name"], e.data["status"]) for e in steps] == expected
    assert events[-1].event == "done"
    assert events[-1].data == {"scan_id": scan_id, "status": "done"}
    assert [e.id for e in events] == list(range(1, len(events) + 1))


async def test_get_scan_after_done_returns_the_persisted_result(tmp_path: Path) -> None:
    async with running_app(make_settings(tmp_path)) as (client, _):
        scan_id = (await client.post("/api/scans", json={"url": DEMO})).json()["scan_id"]
        await read_events(client, scan_id)
        response = await client.get(f"/api/scans/{scan_id}")

    assert response.status_code == 200
    scan = Scan.model_validate(response.json())
    assert scan.status is ScanStatus.DONE
    assert scan.host == "demo.test"
    assert scan.before is not None
    assert scan.before.scores.combined == 39
    assert scan.after is None and scan.patch is None and scan.error is None


async def test_weights_reach_the_job(tmp_path: Path) -> None:
    control = StubControl()
    async with running_app(make_settings(tmp_path), control=control) as (client, _):
        body = {"url": DEMO, "weights": {"a11y": 0.3, "carbon": 0.7}}
        scan_id = (await client.post("/api/scans", json=body)).json()["scan_id"]
        await read_events(client, scan_id)
    assert control.specs[0].weights.a11y == 0.3
    assert control.specs[0].screenshot_dir == tmp_path / "data" / "screenshots" / scan_id


# --------------------------------------------------------------------------- #
# SSRF: URL_BLOCKED before anything is queued
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:22",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "http://[::1]/",
        "http://0x7f000001/",
        "ftp://example.com/file",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "not a url",
        "",
    ],
)
async def test_blocked_urls_are_refused_with_url_blocked(tmp_path: Path, url: str) -> None:
    control = StubControl()
    settings = make_settings(tmp_path, allowed_local_hosts=())
    async with running_app(settings, control=control, preflight=_default_preflight) as (client, _):
        response = await client.post("/api/scans", json={"url": url})
        history = (await client.get("/api/history")).json()
    assert_envelope(response, 400, ErrorCode.URL_BLOCKED)
    assert control.specs == [], "nothing may be queued for a blocked URL"
    assert history["scans"] == []


async def test_missing_url_is_url_blocked(tmp_path: Path) -> None:
    async with running_app(make_settings(tmp_path)) as (client, _):
        response = await client.post("/api/scans", json={})
    assert_envelope(response, 400, ErrorCode.URL_BLOCKED)


async def test_redirect_to_a_private_address_is_url_blocked(tmp_path: Path) -> None:
    """A permitted host that 302s to a private address is refused at POST."""
    control = StubControl()
    with RedirectServer("http://127.0.0.2:9/secret") as server:
        settings = make_settings(tmp_path, allowed_local_hosts=(f"localhost:{server.port}",))
        async with running_app(settings, control=control, preflight=_default_preflight) as (
            client,
            _,
        ):
            response = await client.post(
                "/api/scans", json={"url": f"http://localhost:{server.port}/start"}
            )
    error = assert_envelope(response, 400, ErrorCode.URL_BLOCKED)
    assert "127.0.0.2" in error["message"]
    assert server.hits == [("/start", f"localhost:{server.port}")]
    assert control.specs == []


async def test_redirect_to_metadata_after_two_hops_is_url_blocked(tmp_path: Path) -> None:
    with (
        RedirectServer("http://169.254.169.254/latest/meta-data/") as last,
        RedirectServer(lambda _: f"http://localhost:{last.port}/hop2") as first,
    ):
        allowed = (f"localhost:{first.port}", f"localhost:{last.port}")
        settings = make_settings(tmp_path, allowed_local_hosts=allowed)
        async with running_app(settings, preflight=_default_preflight) as (client, _):
            response = await client.post(
                "/api/scans", json={"url": f"http://localhost:{first.port}/"}
            )
    error = assert_envelope(response, 400, ErrorCode.URL_BLOCKED)
    assert "169.254.169.254" in error["message"]
    assert len(first.hits) == 1 and len(last.hits) == 1


async def test_redirect_within_permitted_hosts_is_accepted(tmp_path: Path) -> None:
    with (
        RedirectServer("") as final,
        RedirectServer(lambda _: f"http://localhost:{final.port}/landing") as first,
    ):
        allowed = (f"localhost:{first.port}", f"localhost:{final.port}")
        settings = make_settings(tmp_path, allowed_local_hosts=allowed)
        async with running_app(settings, preflight=_default_preflight) as (client, _):
            response = await client.post(
                "/api/scans", json={"url": f"http://localhost:{first.port}/"}
            )
    assert response.status_code == 202, response.text
    assert final.hits == [("/landing", f"localhost:{final.port}")]


async def test_unreachable_permitted_host_is_accepted_and_fails_in_the_scan(
    tmp_path: Path,
) -> None:
    """A pre-flight network error is not a verdict; the scan reports it later."""
    settings = make_settings(tmp_path, allowed_local_hosts=("localhost:9",))
    async with running_app(settings, preflight=_default_preflight) as (client, _):
        response = await client.post("/api/scans", json={"url": "http://localhost:9/"})
    assert response.status_code == 202, response.text


# --------------------------------------------------------------------------- #
# Validation errors use the envelope too
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "weights",
    [
        {"a11y": 0.9, "carbon": 0.1},  # outside the 0.3-0.7 UI range (§9.3)
        {"a11y": 0.2, "carbon": 0.8},
        {"a11y": 0.5, "carbon": 0.6},  # does not sum to 1
    ],
)
async def test_bad_weights_are_invalid_request(tmp_path: Path, weights: dict) -> None:
    async with running_app(make_settings(tmp_path)) as (client, _):
        response = await client.post("/api/scans", json={"url": DEMO, "weights": weights})
    assert_envelope(response, 422, ErrorCode.INVALID_REQUEST)


async def test_non_json_body_is_invalid_request(tmp_path: Path) -> None:
    async with running_app(make_settings(tmp_path)) as (client, _):
        response = await client.post(
            "/api/scans", content=b"url=x", headers={"Content-Type": "text/plain"}
        )
    assert_envelope(response, 422, ErrorCode.INVALID_REQUEST)


async def test_unknown_fields_are_rejected(tmp_path: Path) -> None:
    async with running_app(make_settings(tmp_path)) as (client, _):
        response = await client.post("/api/scans", json={"url": DEMO, "admin": True})
    assert_envelope(response, 422, ErrorCode.INVALID_REQUEST)


@pytest.mark.parametrize(
    "path",
    [
        "/api/scans/nope",
        "/api/scans/nope/events",
        "/api/scans/nope/screenshot",
        "/api/no-such-route",
    ],
)
async def test_unknown_ids_and_routes_are_not_found(tmp_path: Path, path: str) -> None:
    async with running_app(make_settings(tmp_path)) as (client, _):
        response = await client.get(path)
    assert_envelope(response, 404, ErrorCode.NOT_FOUND)


async def test_cancel_unknown_scan_is_not_found(tmp_path: Path) -> None:
    async with running_app(make_settings(tmp_path)) as (client, _):
        response = await client.post("/api/scans/nope/cancel")
    assert_envelope(response, 404, ErrorCode.NOT_FOUND)


async def test_wrong_method_keeps_its_status_in_the_envelope(tmp_path: Path) -> None:
    async with running_app(make_settings(tmp_path)) as (client, _):
        response = await client.delete("/api/scans")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == ErrorCode.INVALID_REQUEST.value


# --------------------------------------------------------------------------- #
# Rate limit: 10 scans/min/IP
# --------------------------------------------------------------------------- #


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_eleventh_scan_in_a_minute_is_rate_limited(tmp_path: Path) -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(clock=clock)
    async with running_app(make_settings(tmp_path), limiter=limiter) as (client, _):
        codes = [
            (await client.post("/api/scans", json={"url": DEMO})).status_code for _ in range(10)
        ]
        refused = await client.post("/api/scans", json={"url": DEMO})
        clock.now += 60
        after_window = await client.post("/api/scans", json={"url": DEMO})

    assert codes == [202] * 10
    assert_envelope(refused, 429, ErrorCode.RATE_LIMITED)
    assert refused.headers["retry-after"] == "60"
    assert after_window.status_code == 202


async def test_rate_limit_is_per_ip(tmp_path: Path) -> None:
    limiter = SlidingWindowLimiter(limit=1, clock=FakeClock())
    settings = make_settings(tmp_path)
    async with running_app(settings, limiter=limiter, client_ip="198.51.100.1") as (a, _):
        assert (await a.post("/api/scans", json={"url": DEMO})).status_code == 202
        assert (await a.post("/api/scans", json={"url": DEMO})).status_code == 429
    async with running_app(settings, limiter=limiter, client_ip="198.51.100.2") as (b, _):
        assert (await b.post("/api/scans", json={"url": DEMO})).status_code == 202


async def test_blocked_urls_count_toward_the_limit(tmp_path: Path) -> None:
    limiter = SlidingWindowLimiter(limit=2, clock=FakeClock())
    settings = make_settings(tmp_path, allowed_local_hosts=())
    async with running_app(settings, limiter=limiter) as (client, _):
        for _ in range(2):
            await client.post("/api/scans", json={"url": "http://127.0.0.1:22"})
        response = await client.post("/api/scans", json={"url": "http://127.0.0.1:22"})
    assert_envelope(response, 429, ErrorCode.RATE_LIMITED)


# --------------------------------------------------------------------------- #
# Concurrency: MAX_CONCURRENT_SCANS=2, the third queues
# --------------------------------------------------------------------------- #


async def test_two_scans_run_in_parallel_and_a_third_queues(tmp_path: Path) -> None:
    control = StubControl(gate=asyncio.Event())
    async with running_app(make_settings(tmp_path), control=control) as (client, app):
        runner = app.state.greenaccess.runner
        ids = [
            (await client.post("/api/scans", json={"url": DEMO})).json()["scan_id"]
            for _ in range(3)
        ]
        await wait_until(lambda: len(runner.running) == 2 and len(control.started) == 2)
        await asyncio.sleep(0.05)  # give a wrongly-unblocked third a chance to start

        statuses = [(await client.get(f"/api/scans/{i}")).json()["status"] for i in ids]
        assert statuses == ["running", "running", "queued"]
        assert runner.queued == {ids[2]}
        assert control.max_active == 2

        control.gate.set()
        results = await asyncio.gather(*(read_events(client, i) for i in ids))

    assert all(events[-1].event == "done" for events in results)
    assert control.max_active == 2, "never more than two at once"
    assert control.started[2] == ids[2]


# --------------------------------------------------------------------------- #
# Failures always end the stream cleanly
# --------------------------------------------------------------------------- #


async def test_timeout_produces_an_error_event_and_a_failed_scan(tmp_path: Path) -> None:
    control = StubControl(hang=True)
    settings = make_settings(tmp_path, scan_timeout_s=1)
    async with running_app(settings, control=control) as (client, _):
        scan_id = (await client.post("/api/scans", json={"url": DEMO})).json()["scan_id"]
        events = await read_events(client, scan_id)
        scan = (await client.get(f"/api/scans/{scan_id}")).json()

    assert events[-1].event == "error"
    assert events[-1].data["code"] == "TIMEOUT"
    assert "1 s" in events[-1].data["message"]
    assert scan["status"] == "error"
    assert scan["error"]["code"] == "TIMEOUT"
    assert control.closed == [scan_id], "the pipeline generator must be closed"


@pytest.mark.parametrize("code", ["PAGE_TOO_LARGE", "NAV_FAILED", "URL_BLOCKED", "TIMEOUT"])
async def test_pipeline_failures_keep_their_code(tmp_path: Path, code: str) -> None:
    control = StubControl(fail=ScanFailed(code, f"stub says {code}"))
    async with running_app(make_settings(tmp_path), control=control) as (client, _):
        scan_id = (await client.post("/api/scans", json={"url": DEMO})).json()["scan_id"]
        events = await read_events(client, scan_id)
        scan = (await client.get(f"/api/scans/{scan_id}")).json()

    assert [e.event for e in events].count("error") == 1
    assert events[-1].event == "error"
    assert events[-1].data == {"code": code, "message": f"stub says {code}"}
    assert "done" not in [e.event for e in events]
    assert scan["status"] == "error"
    assert scan["error"] == {"code": code, "message": f"stub says {code}"}
    assert scan["before"] is None


async def test_an_unexpected_exception_still_ends_the_stream(tmp_path: Path) -> None:
    control = StubControl(fail=RuntimeError("bug"))
    async with running_app(make_settings(tmp_path), control=control) as (client, _):
        scan_id = (await client.post("/api/scans", json={"url": DEMO})).json()["scan_id"]
        events = await read_events(client, scan_id)
    assert events[-1].event == "error"
    assert events[-1].data["code"] == "NAV_FAILED"
    assert "bug" not in events[-1].data["message"], "internal detail stays in the log"


async def test_cancel_a_running_scan(tmp_path: Path) -> None:
    control = StubControl(hang=True)
    async with running_app(make_settings(tmp_path), control=control) as (client, _):
        scan_id = (await client.post("/api/scans", json={"url": DEMO})).json()["scan_id"]
        await wait_until(lambda: control.started == [scan_id])
        response = await client.post(f"/api/scans/{scan_id}/cancel")
        events = await read_events(client, scan_id)
        again = await client.post(f"/api/scans/{scan_id}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert response.json()["error"]["code"] == "CANCELLED"
    assert events[-1].event == "error" and events[-1].data["code"] == "CANCELLED"
    assert control.closed == [scan_id]
    assert again.status_code == 200 and again.json()["error"]["code"] == "CANCELLED"


async def test_cancel_a_queued_scan(tmp_path: Path) -> None:
    control = StubControl(hang=True)
    settings = make_settings(tmp_path, max_concurrent_scans=1)
    async with running_app(settings, control=control) as (client, _):
        first = (await client.post("/api/scans", json={"url": DEMO})).json()["scan_id"]
        second = (await client.post("/api/scans", json={"url": DEMO})).json()["scan_id"]
        await wait_until(lambda: control.started == [first])
        response = await client.post(f"/api/scans/{second}/cancel")
    assert response.json()["status"] == "error"
    assert response.json()["error"]["code"] == "CANCELLED"
    assert second not in control.started, "a cancelled queued scan never runs"


async def test_cancelling_a_finished_scan_leaves_it_alone(tmp_path: Path) -> None:
    async with running_app(make_settings(tmp_path)) as (client, _):
        scan_id = (await client.post("/api/scans", json={"url": DEMO})).json()["scan_id"]
        await read_events(client, scan_id)
        response = await client.post(f"/api/scans/{scan_id}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "done"


async def test_shutdown_cancels_running_scans(tmp_path: Path) -> None:
    control = StubControl(hang=True)
    settings = make_settings(tmp_path)
    async with running_app(settings, control=control) as (client, _):
        scan_id = (await client.post("/api/scans", json={"url": DEMO})).json()["scan_id"]
        await wait_until(lambda: control.started == [scan_id])
    scan = await ScanRepository(settings.database_url).get(scan_id)
    assert scan is not None and scan.status is ScanStatus.ERROR
    assert scan.error is not None and scan.error.code is ErrorCode.CANCELLED


# --------------------------------------------------------------------------- #
# Replay for late subscribers
# --------------------------------------------------------------------------- #


async def test_late_subscribers_replay_the_whole_stream(tmp_path: Path) -> None:
    async with running_app(make_settings(tmp_path)) as (client, _):
        scan_id = (await client.post("/api/scans", json={"url": DEMO})).json()["scan_id"]
        first = await read_events(client, scan_id)
        second = await read_events(client, scan_id)
    assert first == second
    assert first[0].data["name"] == "validate"


async def test_subscriber_joining_mid_scan_sees_earlier_steps(tmp_path: Path) -> None:
    control = StubControl(gate=asyncio.Event())
    async with running_app(make_settings(tmp_path), control=control) as (client, _):
        scan_id = (await client.post("/api/scans", json={"url": DEMO})).json()["scan_id"]
        await wait_until(lambda: control.started == [scan_id])
        reader = asyncio.create_task(read_events(client, scan_id))
        await asyncio.sleep(0.05)
        control.gate.set()
        events = await reader
    names = [(e.data.get("name"), e.data.get("status")) for e in events if e.event == "step"]
    assert names[:3] == [("validate", "running"), ("validate", "ok"), ("load", "running")]
    assert events[-1].event == "done"


async def test_last_event_id_resumes_without_duplicates(tmp_path: Path) -> None:
    async with running_app(make_settings(tmp_path)) as (client, _):
        scan_id = (await client.post("/api/scans", json={"url": DEMO})).json()["scan_id"]
        everything = await read_events(client, scan_id)
        resumed = await read_events(client, scan_id, headers={"Last-Event-ID": "5"})
    assert resumed == everything[5:]


async def test_finished_scans_stream_from_the_database_after_restart(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    async with running_app(settings) as (client, _):
        scan_id = (await client.post("/api/scans", json={"url": DEMO})).json()["scan_id"]
        await read_events(client, scan_id)
    async with running_app(settings) as (client, _):
        events = await read_events(client, scan_id)
        scan = (await client.get(f"/api/scans/{scan_id}")).json()
    assert [e.event for e in events] == ["done"]
    assert scan["status"] == "done" and scan["before"]["scores"]["combined"] == 39


async def test_scans_interrupted_by_a_restart_are_marked_failed(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repository = ScanRepository(settings.database_url)
    repository.create_all()
    await repository.insert(Scan(id="a" * 32, url=DEMO, host="demo.test"))
    await repository.mark_running("a" * 32)
    repository.dispose()

    async with running_app(settings) as (client, _):
        scan = (await client.get(f"/api/scans/{'a' * 32}")).json()
        events = await read_events(client, "a" * 32)
    assert scan["status"] == "error"
    assert scan["error"]["code"] == "CANCELLED"
    assert events[-1].event == "error"


# --------------------------------------------------------------------------- #
# Screenshot
# --------------------------------------------------------------------------- #


async def test_screenshot_is_served_as_png(tmp_path: Path) -> None:
    control = StubControl(write_screenshot=True)
    async with running_app(make_settings(tmp_path), control=control) as (client, _):
        scan_id = (await client.post("/api/scans", json={"url": DEMO})).json()["scan_id"]
        await read_events(client, scan_id)
        before = await client.get(f"/api/scans/{scan_id}/screenshot")
        explicit = await client.get(f"/api/scans/{scan_id}/screenshot?state=before")
        after = await client.get(f"/api/scans/{scan_id}/screenshot?state=after")
        bogus = await client.get(f"/api/scans/{scan_id}/screenshot?state=sideways")

    assert before.status_code == 200
    assert before.headers["content-type"] == "image/png"
    assert before.content == PNG_1X1
    assert explicit.content == PNG_1X1
    assert (tmp_path / "data" / "screenshots" / scan_id / "page.png").is_file()
    assert_envelope(after, 404, ErrorCode.NOT_FOUND)
    assert_envelope(bogus, 422, ErrorCode.INVALID_REQUEST)


async def test_screenshot_outside_the_screenshot_folder_is_never_served(tmp_path: Path) -> None:
    secret = tmp_path / "secret.png"
    secret.write_bytes(PNG_1X1)
    control = StubControl(screenshot_override=str(secret))
    async with running_app(make_settings(tmp_path), control=control) as (client, _):
        scan_id = (await client.post("/api/scans", json={"url": DEMO})).json()["scan_id"]
        await read_events(client, scan_id)
        response = await client.get(f"/api/scans/{scan_id}/screenshot")
    assert_envelope(response, 404, ErrorCode.NOT_FOUND)


async def test_screenshot_of_a_running_scan_is_not_found(tmp_path: Path) -> None:
    control = StubControl(gate=asyncio.Event())
    async with running_app(make_settings(tmp_path), control=control) as (client, _):
        scan_id = (await client.post("/api/scans", json={"url": DEMO})).json()["scan_id"]
        response = await client.get(f"/api/scans/{scan_id}/screenshot")
        control.gate.set()
        await read_events(client, scan_id)
    assert_envelope(response, 404, ErrorCode.NOT_FOUND)


# --------------------------------------------------------------------------- #
# History and demo
# --------------------------------------------------------------------------- #


async def test_history_lists_newest_first_with_a_trend_per_host(tmp_path: Path) -> None:
    control = StubControl()
    async with running_app(make_settings(tmp_path), control=control) as (client, _):
        ids = []
        for url, combined in (
            ("http://demo.test/a", 30),
            ("http://other.test/", 50),
            ("http://demo.test/b", 45),
        ):
            control.combined = combined
            scan_id = (await client.post("/api/scans", json={"url": url})).json()["scan_id"]
            await read_events(client, scan_id)
            ids.append(scan_id)
        control.fail = ScanFailed("NAV_FAILED", "down")
        failed = (await client.post("/api/scans", json={"url": DEMO})).json()["scan_id"]
        await read_events(client, failed)

        everything = (await client.get("/api/history")).json()
        demo_only = (await client.get("/api/history", params={"host": "DEMO.test"})).json()

    assert [s["id"] for s in everything["scans"]] == [failed, ids[2], ids[1], ids[0]]
    assert everything["host"] is None
    failed_row = everything["scans"][0]
    assert failed_row["status"] == "error" and failed_row["combined"] is None
    assert everything["trends"]["demo.test"] == [
        {"scan_id": ids[0], "created_at": everything["scans"][3]["created_at"], "combined": 30},
        {"scan_id": ids[2], "created_at": everything["scans"][1]["created_at"], "combined": 45},
    ]
    assert [p["combined"] for p in everything["trends"]["other.test"]] == [50]

    assert demo_only["host"] == "demo.test"
    assert {s["host"] for s in demo_only["scans"]} == {"demo.test"}
    assert set(demo_only["trends"]) == {"demo.test"}


async def test_history_limit_is_bounded(tmp_path: Path) -> None:
    async with running_app(make_settings(tmp_path)) as (client, _):
        response = await client.get("/api/history", params={"limit": 0})
    assert_envelope(response, 422, ErrorCode.INVALID_REQUEST)


async def test_demo_returns_the_demo_url(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, demo_url="http://localhost:8081")
    async with running_app(settings) as (client, _):
        response = await client.get("/api/demo")
    assert response.status_code == 200
    assert response.json() == {"url": "http://localhost:8081"}


# --------------------------------------------------------------------------- #
# CORS and OpenAPI
# --------------------------------------------------------------------------- #


async def test_cors_allows_the_frontend_dev_origin(tmp_path: Path) -> None:
    async with running_app(make_settings(tmp_path)) as (client, _):
        allowed = await client.options(
            "/api/scans",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        other = await client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-origin" not in other.headers


async def test_openapi_types_every_response(tmp_path: Path) -> None:
    async with running_app(make_settings(tmp_path)) as (client, _):
        spec = (await client.get("/openapi.json")).json()
    schemas = spec["components"]["schemas"]
    for name in ("Scan", "ScanCreated", "HistoryResponse", "DemoInfo", "ApiErrorEnvelope"):
        assert name in schemas, name

    def ref(path: str, method: str, status: str) -> str:
        content = spec["paths"][path][method]["responses"][status]["content"]
        return content["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]

    assert ref("/api/scans", "post", "202") == "ScanCreated"
    assert ref("/api/scans", "post", "400") == "ApiErrorEnvelope"
    assert ref("/api/scans", "post", "429") == "ApiErrorEnvelope"
    assert ref("/api/scans/{scan_id}", "get", "200") == "Scan"
    assert ref("/api/history", "get", "200") == "HistoryResponse"
    assert ref("/api/demo", "get", "200") == "DemoInfo"
