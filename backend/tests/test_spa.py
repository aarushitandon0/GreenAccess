"""Serving the built frontend from the API process (app/api/spa.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.api.spa import resolve_static_dir
from tests.api_support import make_settings, running_app

INDEX = "<!doctype html><title>GreenAccess</title><div id=root></div>"


@pytest.fixture
def built(tmp_path: Path) -> Path:
    """A directory shaped like a real `npm run build` output."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX, encoding="utf-8")
    (dist / "assets" / "index-abc123.js").write_text("export default 1", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    return dist


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


def test_nothing_is_served_when_it_is_not_configured() -> None:
    """A developer machine must never pick up a stale frontend/dist."""
    assert resolve_static_dir(None) is None


def test_a_configured_build_is_used(built: Path) -> None:
    assert resolve_static_dir(built) == built


def test_a_configured_directory_without_an_index_is_refused(tmp_path: Path) -> None:
    empty = tmp_path / "nope"
    empty.mkdir()
    assert resolve_static_dir(empty) is None


def test_a_configured_directory_that_is_missing_is_refused(tmp_path: Path) -> None:
    assert resolve_static_dir(tmp_path / "does-not-exist") is None


# --------------------------------------------------------------------------- #
# Serving
# --------------------------------------------------------------------------- #


async def test_the_app_is_served_at_the_root(tmp_path: Path, built: Path) -> None:
    settings = make_settings(tmp_path, static_dir=built)
    async with running_app(settings) as (client, _):
        response = await client.get("/")
    assert response.status_code == 200
    assert "GreenAccess" in response.text


async def test_an_unknown_path_falls_back_to_the_app(tmp_path: Path, built: Path) -> None:
    """Every chapter is a fragment of one page, so any path is still the app."""
    settings = make_settings(tmp_path, static_dir=built)
    async with running_app(settings) as (client, _):
        response = await client.get("/some/deep/link")
    assert response.status_code == 200
    assert "GreenAccess" in response.text


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/assets/index-abc123.js", "public, max-age=31536000, immutable"),
        ("/index.html", "no-cache"),
    ],
)
async def test_hashed_assets_are_immutable_and_the_shell_is_not(
    tmp_path: Path, built: Path, path: str, expected: str
) -> None:
    settings = make_settings(tmp_path, static_dir=built)
    async with running_app(settings) as (client, _):
        response = await client.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == expected


async def test_the_api_still_answers_json_under_the_mount(tmp_path: Path, built: Path) -> None:
    """The root mount must not shadow the routers registered before it."""
    settings = make_settings(tmp_path, static_dir=built)
    async with running_app(settings) as (client, _):
        health = await client.get("/api/health")
        demo = await client.get("/api/demo")
    assert health.status_code == 200 and health.json()["status"] == "ok"
    assert demo.status_code == 200 and "url" in demo.json()


async def test_an_unknown_api_path_is_json_not_the_app(tmp_path: Path, built: Path) -> None:
    """Falling back to index.html here would hand a client HTML to JSON.parse."""
    settings = make_settings(tmp_path, static_dir=built)
    async with running_app(settings) as (client, _):
        response = await client.get("/api/not-a-real-endpoint")
    assert response.status_code == 404
    assert "GreenAccess" not in response.text


async def test_the_api_is_unaffected_when_no_ui_is_configured(tmp_path: Path) -> None:
    async with running_app(make_settings(tmp_path)) as (client, _):
        health = await client.get("/api/health")
        root = await client.get("/")
    assert health.status_code == 200
    assert root.status_code == 404
