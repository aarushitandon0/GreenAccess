"""The /api/health contract (MASTERSPEC §12)."""

from fastapi.testclient import TestClient

from app.main import create_app


def test_health_returns_ok_and_engine_versions():
    client = TestClient(create_app())
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"]
    # engine_versions must always carry the three keys named in MASTERSPEC §5.
    assert set(body["engines"]) == {"playwright", "axe", "swd_model"}


def test_health_reports_the_pinned_playwright_version():
    client = TestClient(create_app())
    assert client.get("/api/health").json()["engines"]["playwright"] == "1.63.0"
