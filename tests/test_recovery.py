"""Transient infrastructure errors leave a run retryable instead of failing it."""

import pytest

from app.agent import create_run, execute_run
from app.config import settings
from app.leases import claim
from tests.support import _free_port, effect_stats, expire_lease, load_run

pytestmark = pytest.mark.usefixtures("db")


def test_downstream_outage_abandons_the_run_and_a_later_claim_finishes_it(monkeypatch):
    run_id = create_run(objective="downstream outage", repo_path="sample_repo")
    healthy_url = settings.mock_effects_url

    # Nothing listens here: report.publish gets a connection error.
    monkeypatch.setattr(settings, "mock_effects_url", f"http://127.0.0.1:{_free_port()}")
    assert execute_run(claim("worker-a")) == "ABANDONED"

    run, steps, calls, _ = load_run(run_id)
    assert run.status == "RUNNING"  # not FAILED: the run itself did nothing wrong
    publish = next(c for c in calls if c.tool == "report.publish")
    assert publish.status == "INTENDED"

    # The downstream recovers; once the lease expires, a claim re-runs the
    # keyed effect with the same idempotency key and finishes.
    monkeypatch.setattr(settings, "mock_effects_url", healthy_url)
    expire_lease(run_id)
    assert execute_run(claim("worker-b")) == "COMPLETED"

    run, _, calls, _ = load_run(run_id)
    assert run.lease_generation == 2
    assert next(c for c in calls if c.tool == "report.publish").idempotency_key == (
        publish.idempotency_key
    )
    assert effect_stats()["reports_applied"] == 1
