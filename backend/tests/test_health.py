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


def test_health_reports_seichi_mode():
    app.dependency_overrides[get_db_health] = lambda: "up"
    client = TestClient(app)

    response = client.get("/api/health")

    # testsupport 固定 MEGURI_SEICHI_MODE=file（离线兜底默认）；
    # LLM/交通/语料库已无模式开关，health 只报告 seichi
    assert response.json()["adapters"] == {"seichi": "file"}
