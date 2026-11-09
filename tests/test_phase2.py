# tests/test_phase2.py
"""Workers and leases: no run is ever owned by two workers."""

import threading
import time
from collections import Counter

import httpx
import pytest

from app.agent import create_run, execute_run
from app.config import settings
from app.leases import Heartbeat, LostLease, claim, fenced, renew
from app.model_interface import FakeModel
from app.models import AgentRun
from app.worker import default_worker_id
from tests.support import (
    count_status,
    effect_stats,
    expire_lease,
    load_run,
    start_worker,
    wait_for,
)

pytestmark = pytest.mark.usefixtures("db")


def new_run(name="lease test"):
    return create_run(objective=name, repo_path="sample_repo")


def test_two_workers_race_for_one_run_and_only_one_wins():
    run_id = new_run()
    barrier = threading.Barrier(8)
    results = []

    def race(i):
        barrier.wait()
        results.append(claim(f"racer-{i}"))

    threads = [threading.Thread(target=race, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    winners = [lease for lease in results if lease is not None]
    assert len(winners) == 1
    assert winners[0].run_id == run_id
    assert winners[0].generation == 1

    _, _, _, events = load_run(run_id)
    assert [e.type for e in events].count("run_claimed") == 1


def test_a_commit_with_a_stale_generation_is_rejected():
    run_id = new_run()
    old = claim("worker-a")

    expire_lease(run_id)
    new = claim("worker-b")
    assert new.generation == old.generation + 1

    with pytest.raises(LostLease):
        with fenced(old) as session:
            session.get(AgentRun, run_id).error = "zombie write"

    assert old.lost.is_set()
    assert renew(old) is False

    run, *_ = load_run(run_id)
    assert run.error is None
    assert run.lease_owner == "worker-b"

    # The new owner can still write.
    with fenced(new) as session:
        session.get(AgentRun, run_id).error = "owner write"
    assert load_run(run_id)[0].error == "owner write"


def test_a_stopped_heartbeat_lets_another_worker_take_over(monkeypatch):
    monkeypatch.setattr(settings, "lease_seconds", 0.5)
    run_id = new_run()

    first = claim("worker-a")  # and never heartbeats
    assert claim("worker-b") is None  # lease still live

    time.sleep(0.7)
    second = claim("worker-b")

    assert second is not None
    assert second.run_id == run_id
    assert second.generation == first.generation + 1


def test_a_running_heartbeat_keeps_the_lease(monkeypatch):
    monkeypatch.setattr(settings, "lease_seconds", 0.6)
    new_run()
    lease = claim("worker-a")

    heartbeat = Heartbeat(lease, interval=0.15)
    heartbeat.start()
    try:
        time.sleep(1.5)
        assert claim("worker-b") is None
        assert not lease.lost.is_set()
    finally:
        heartbeat.stop()


def test_a_worker_that_loses_its_lease_stops_writing():
    run_id = new_run()
    lease = claim("worker-a")
    script = FakeModel(settings.model_script)
    taken = {}

    class TakeoverModel:
        """Another worker takes the run over while worker-a is deciding step 2."""

        def decide(self, seq, context):
            if seq == 2 and not taken:
                expire_lease(run_id)
                taken["lease"] = claim("worker-b")
            return script.decide(seq, context)

    assert execute_run(lease, TakeoverModel()) == "LOST"

    run, steps, _, _ = load_run(run_id)
    assert run.status == "RUNNING"
    assert run.lease_owner == "worker-b"
    # Step 1 was written under generation 1; step 2 never was.
    assert [s.seq for s in steps] == [1]


def test_twenty_runs_across_three_workers_with_no_run_owned_twice(tmp_path):
    run_ids = [new_run(f"batch run {i}") for i in range(20)]

    workers = [
        start_worker(tmp_path, worker_id=f"worker-{i}", exit_when_idle=True) for i in range(3)
    ]
    try:
        for worker in workers:
            assert worker.wait(timeout=180) == 0, worker.log()
    finally:
        for worker in workers:
            worker.stop()

    wait_for(lambda: count_status("COMPLETED") == 20, timeout=5, what="20 completed runs")

    owners = Counter()
    for run_id in run_ids:
        run, _, _, events = load_run(run_id)
        claims = [e for e in events if e.type == "run_claimed"]
        assert len(claims) == 1, f"run {run_id} claimed {len(claims)} times"
        assert run.lease_generation == 1
        owners[run.lease_owner] += 1

    assert len(owners) >= 2, f"work wasn't shared: {owners}"
    stats = effect_stats()
    assert stats["reports_applied"] == 20
    assert stats["duplicates_suppressed"] == 0


def test_worker_ids_are_unique_on_a_shared_host(monkeypatch):
    monkeypatch.delenv("ECS_CONTAINER_METADATA_URI_V4", raising=False)
    monkeypatch.setattr("os.getpid", lambda: 1)  # every container is PID 1

    assert default_worker_id() != default_worker_id()


def test_worker_id_is_the_ecs_task_id(monkeypatch):
    monkeypatch.setenv("ECS_CONTAINER_METADATA_URI_V4", "http://metadata.test")
    response = httpx.Response(
        200,
        json={"TaskARN": "arn:aws:ecs:us-east-1:123:task/agentforge/abc123"},
        request=httpx.Request("GET", "http://metadata.test/task"),
    )
    monkeypatch.setattr("app.worker.httpx.get", lambda url, timeout: response)

    assert default_worker_id() == "abc123"
