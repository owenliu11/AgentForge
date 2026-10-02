from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_ok(monkeypatch):
    monkeypatch.setattr("app.main.ping_db", lambda: True)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "db": "up",
    }


def test_health_degraded(monkeypatch):
    monkeypatch.setattr("app.main.ping_db", lambda: False)

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "db": "down",
    }
