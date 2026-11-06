"""The demo site drives real worker processes through the same scenarios as the drills."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import demo
from app.main import app as main_app
from tests.support import effect_stats, load_run, wait_for

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(demo, "_session", None)
    app = FastAPI()
    app.include_router(demo.router)
    return TestClient(app)


def play(client, key):
    response = client.post(f"/demo/api/scenarios/{key}")
    assert response.status_code == 202, response.text
    return wait_for(
        lambda: (state := client.get("/demo/api/state").json())["finished"] and state,
        timeout=60,
        what=f"scenario {key}",
    )


def test_demo_is_off_by_default():
    assert TestClient(main_app).get("/demo").status_code == 404


def test_page_and_scenarios_are_served(client):
    assert "AgentForge demo" in client.get("/demo").text
    assert "crash_after_effect" in [s["key"] for s in client.get("/demo/api/scenarios").json()]
    assert client.get("/demo/api/state").json() is None
    assert client.post("/demo/api/scenarios/nope").status_code == 404


def test_crash_scenario_recovers_with_one_applied_report(client):
    state = play(client, "crash_after_effect")

    assert state["error"] is None
    assert state["run"]["status"] == "COMPLETED"
    assert state["run"]["lease_owner"] == "worker-b"
    assert state["run"]["lease_generation"] == 2
    assert [w["state"] for w in state["workers"]] == ["crashed", "finished"]
    assert state["effects"]["reports_applied"] == 1
    assert state["effects"]["duplicates_suppressed"] == 1
    assert "test_subtract_fails" in state["report"]


def test_stale_worker_scenario_fences_off_the_frozen_worker(client):
    state = play(client, "stale_worker")

    assert state["error"] is None
    assert state["run"]["status"] == "COMPLETED"
    assert [w["state"] for w in state["workers"]] == ["fenced off", "finished"]
    assert load_run(state["run"]["id"])[3][-1].type == "run_completed"
    assert effect_stats()["reports_applied"] == 1


def test_only_one_scenario_runs_at_a_time(client):
    assert client.post("/demo/api/scenarios/normal").status_code == 202
    assert client.post("/demo/api/scenarios/normal").status_code == 409
    wait_for(lambda: client.get("/demo/api/state").json()["finished"], timeout=60, what="finish")
