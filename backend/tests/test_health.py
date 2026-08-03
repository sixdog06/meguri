from fastapi.testclient import TestClient

from app.api.health import get_db_health
from app.main import app


def test_health_reports_ok_when_db_is_up():
    app.dependency_overrides[get_db_health] = lambda: "up"
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["services"]["db"] == "up"


def test_health_reports_degraded_when_db_is_down():
    app.dependency_overrides[get_db_health] = lambda: "down"
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["services"]["db"] == "down"


def test_health_reports_adapter_modes():
    app.dependency_overrides[get_db_health] = lambda: "up"
    client = TestClient(app)

    response = client.get("/api/health")

    # conftest 固定 MEGURI_SEICHI_MODE=fake；LLM adapter_mode 默认 fake
    assert response.json()["adapters"] == {"llm": "fake", "seichi": "fake"}
