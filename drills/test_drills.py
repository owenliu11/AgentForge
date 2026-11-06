# drills/test_drills.py
"""
Kill drills: kill a worker at any point; another worker finishes the run with
zero duplicate side effects.

Each drill runs real worker processes against Postgres and mock_effects.
Worker A gets AF_CRASH_AT and dies with os._exit(137) at that line; worker B
waits for A's lease to expire, claims generation 2, and resumes.
"""

import os
import signal
import time
from pathlib import Path

import pytest

from app.agent import CRASH_POINTS, create_run
from tests.support import TERMINAL, effect_stats, load_run, run_status, start_worker, wait_for

pytestmark = pytest.mark.usefixtures("db")

DRILL_SCRIPTS = Path(__file__).parent / "scripts"

# How many times report.publish reached the downstream. A crash after the
# effect means B re-sends it with the same key and the downstream dedupes it.
EXPECTED_DELIVERIES = {
    "after_decision": 1,
    "after_intent": 1,
    "after_effect": 2,
    "before_commit": 2,
}


def finish_with(worker_env, tmp_path, run_id, timeout=60):
    """Start a healthy worker, wait for the run to finish, stop it."""
    worker = start_worker(tmp_path, worker_id="worker-b", env=worker_env)
    try:
        wait_for(lambda: run_status(run_id) in TERMINAL, timeout=timeout, what="run to finish")
    finally:
        worker.stop()
    return worker


def assert_events_contiguous(events):
    assert [e.seq for e in events] == list(range(1, len(events) + 1))


@pytest.mark.parametrize("crash_at", CRASH_POINTS)
def test_crash_at_each_point_recovers_with_zero_duplicate_effects(crash_at, tmp_path):
    run_id = create_run(objective=f"drill {crash_at}", repo_path="sample_repo")

    crasher = start_worker(tmp_path, worker_id="worker-a", env={"AF_CRASH_AT": crash_at})
    assert crasher.wait(timeout=60) == 137, crasher.log()
    assert run_status(run_id) == "RUNNING"

    survivor = finish_with({}, tmp_path, run_id)

    run, steps, calls, events = load_run(run_id)
    types = [e.type for e in events]

    assert run.status == "COMPLETED", (run.error, survivor.log())
    assert run.lease_owner == "worker-b"
    assert run.lease_generation == 2
    assert [s.status for s in steps] == ["SUCCEEDED"] * 5
    assert all(c.status == "SUCCEEDED" for c in calls)
    assert len(calls) == 4  # one record per tool step, never two
    assert "run_resumed" in types
    assert_events_contiguous(events)

    stats = effect_stats()
    assert stats["reports_applied"] == 1  # exactly once
    assert stats["report_deliveries"] == EXPECTED_DELIVERIES[crash_at]

    publish = next(c for c in calls if c.tool == "report.publish")
    assert publish.generation == 2

    resumed = next(e for e in events if e.type == "run_resumed")
    # A decision saved before the crash is replayed, not re-asked.
    assert resumed.payload["replayed_decision"] is True


def test_stale_worker_cannot_write_after_takeover(tmp_path):
    """
    Worker A freezes right after publishing (heartbeat included), as if the
    VM hung. B takes over and finishes. When A wakes up, its write is fenced
    off and it drops the run.
    """
    run_id = create_run(objective="zombie drill", repo_path="sample_repo")
    zombie = start_worker(tmp_path, worker_id="worker-a", env={"AF_STALL_AT": "after_effect"})

    def frozen():
        pid, status = os.waitpid(zombie.proc.pid, os.WNOHANG | os.WUNTRACED)
        return pid != 0 and os.WIFSTOPPED(status)

    try:
        wait_for(frozen, timeout=60, what="worker-a to freeze")
        finish_with({}, tmp_path, run_id)
        assert run_status(run_id) == "COMPLETED"

        os.kill(zombie.proc.pid, signal.SIGCONT)
        wait_for(lambda: "lease_lost" in zombie.log(), timeout=15, what="worker-a to see LostLease")
    finally:
        zombie.stop()

    run, _, calls, events = load_run(run_id)
    assert run.status == "COMPLETED"
    assert run.lease_owner == "worker-b"

    publish = next(c for c in calls if c.tool == "report.publish")
    assert publish.generation == 2  # A's step-4 write never landed

    # A's late write was rejected, so nothing after completion.
    assert events[-1].type == "run_completed"

    stats = effect_stats()
    assert stats["reports_applied"] == 1
    assert stats["duplicates_suppressed"] == 1


def test_poison_run_fails_after_max_attempts(tmp_path):
    """A run that kills every worker that touches it is failed, not retried forever."""
    run_id = create_run(objective="poison drill", repo_path="sample_repo")

    for i in range(3):
        crasher = start_worker(
            tmp_path, worker_id=f"crasher-{i}", env={"AF_CRASH_AT": "after_intent"}
        )
        assert crasher.wait(timeout=60) == 137, crasher.log()

    finish_with({}, tmp_path, run_id)

    run, _, _, events = load_run(run_id)
    assert run.status == "FAILED"
    assert run.error.startswith("POISON_RUN")
    assert run.attempts == 4
    assert [e.type for e in events].count("run_claimed") == 4
    assert effect_stats()["reports_applied"] == 0


def test_unkeyed_effect_with_unknown_outcome_fails_instead_of_retrying(tmp_path):
    """
    notify.post has no idempotency key. If A dies after sending but before
    saving the result, B can't know whether it was sent, so it fails the run
    with UNKNOWN_EFFECT rather than risk a second notification.
    """
    run_id = create_run(objective="unknown effect drill", repo_path="sample_repo")
    env = {"MODEL_SCRIPT": str(DRILL_SCRIPTS / "notify.yaml"), "AF_CRASH_TOOL": "notify.post"}

    crasher = start_worker(
        tmp_path, worker_id="worker-a", env={**env, "AF_CRASH_AT": "after_effect"}
    )
    assert crasher.wait(timeout=60) == 137, crasher.log()

    finish_with(env, tmp_path, run_id)

    run, _, calls, _ = load_run(run_id)
    assert run.status == "FAILED"
    assert run.error.startswith("UNKNOWN_EFFECT")
    assert calls[0].status == "UNKNOWN"
    assert effect_stats()["notifications"] == 1  # sent once, never re-sent


def test_recovery_is_fast(tmp_path):
    """Kill to next claim stays close to the lease length (2 s in drills)."""
    run_id = create_run(objective="timing drill", repo_path="sample_repo")

    crasher = start_worker(tmp_path, worker_id="worker-a", env={"AF_CRASH_AT": "after_effect"})
    assert crasher.wait(timeout=60) == 137
    killed_at = time.time()

    finish_with({}, tmp_path, run_id)

    _, _, _, events = load_run(run_id)
    second_claim = [e for e in events if e.type == "run_claimed"][1]
    recovery = second_claim.created_at.timestamp() - killed_at
    # Lease length plus poll interval, with slack for a slow CI machine.
    assert recovery < 2 + 3, f"recovery took {recovery:.2f}s"
