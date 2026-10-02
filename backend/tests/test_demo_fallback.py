"""The cached-demo mode: serve the recording, and refuse to pass it off as live.

These tests pin the two things that make ``DEMO_FALLBACK`` honest rather than a
mock (CLAUDE.md rule 4):

* the numbers it returns are the recorded ones, unchanged, and
* it refuses any URL but the one the recording is of, instead of answering with
  the Daily Herald's numbers for someone else's site.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.fixtures.loader import DEMO_SCAN_ID, load_demo
from app.main import create_app


@pytest.fixture
def cached_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """An app built with the fallback on."""
    monkeypatch.setenv("DEMO_FALLBACK", "1")
    monkeypatch.setenv("LLM_OFFLINE", "1")
    # config.get_settings is lru_cached; without this the app is built from
    # whatever the first test in the session resolved.
    get_settings.cache_clear()
    load_demo.cache_clear()
    with TestClient(create_app()) as client:
        yield client
    get_settings.cache_clear()
    load_demo.cache_clear()


@pytest.fixture
def recorded_url() -> str:
    return load_demo().before.url


def test_demo_endpoint_says_it_is_cached(cached_client: TestClient) -> None:
    body = cached_client.get("/api/demo").json()
    assert body["cached"] is True
    assert "Not live data" in body["label"]
    assert body["recorded_at"]


def test_scan_returns_the_recorded_before_scores(
    cached_client: TestClient, recorded_url: str
) -> None:
    created = cached_client.post("/api/scans", json={"url": recorded_url})
    assert created.status_code == 202
    assert created.json()["scan_id"] == DEMO_SCAN_ID

    body = cached_client.get(f"/api/scans/{DEMO_SCAN_ID}").json()
    expected = load_demo().before
    assert body["before"]["scores"]["combined"] == expected.before.scores.combined
    assert body["before"]["scores"]["a11y"] == expected.before.scores.a11y
    # The first chapter has not been fixed yet.
    assert body["after"] is None
    assert body["patch"] is None


def test_every_scan_response_is_labelled_not_live(cached_client: TestClient) -> None:
    body = cached_client.get(f"/api/scans/{DEMO_SCAN_ID}").json()
    assert body["_cached"]["live"] is False
    assert "Not live data" in body["_cached"]["label"]


def test_patched_chapter_returns_the_recorded_after_scores(
    cached_client: TestClient,
) -> None:
    body = cached_client.get(f"/api/scans/{DEMO_SCAN_ID}?patched=1").json()
    expected = load_demo().after
    assert expected.after is not None
    assert body["after"]["scores"]["combined"] == expected.after.scores.combined
    assert body["patch"] is not None


def test_a_different_url_is_refused_not_answered(cached_client: TestClient) -> None:
    """The point of the mode: never show one site's numbers for another."""
    response = cached_client.post("/api/scans", json={"url": "https://example.com/"})
    assert response.status_code >= 400
    error = response.json()["error"]
    assert error["code"] == "URL_BLOCKED"
    assert "cached" in error["message"].lower()


def test_fixes_come_from_the_recording_without_an_llm(
    cached_client: TestClient,
) -> None:
    body = cached_client.post(f"/api/scans/{DEMO_SCAN_ID}/fixes", json={}).json()
    recorded = load_demo().after.patch
    assert recorded is not None
    assert len(body["fixes"]) == len(recorded.fixes)
    assert body["_cached"]["live"] is False


def test_unknown_scan_id_is_not_found(cached_client: TestClient) -> None:
    response = cached_client.get("/api/scans/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_event_stream_replays_real_pipeline_step_names(
    cached_client: TestClient,
) -> None:
    from tests.api_support import ALL_STEPS

    with cached_client.stream(
        "GET", f"/api/scans/{DEMO_SCAN_ID}/events"
    ) as response:
        assert response.status_code == 200
        seen: list[str] = []
        done = False
        for line in response.iter_lines():
            if line.startswith("data:"):
                payload = json.loads(line[len("data:") :])
                if "name" in payload:
                    seen.append(payload["name"])
                elif payload.get("scan_id"):
                    done = True
                    break

    assert done, "the stream never sent `done`"
    # Every replayed name must be a step the real pipeline actually emits.
    assert set(seen) <= set(ALL_STEPS), f"invented step names: {set(seen) - set(ALL_STEPS)}"


def test_fallback_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the env var the live routes serve, so nothing is cached by accident."""
    monkeypatch.delenv("DEMO_FALLBACK", raising=False)
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        body = client.get("/api/demo").json()
    assert "cached" not in body
