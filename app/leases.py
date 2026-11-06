# app/leases.py
"""
Leases with fencing tokens.

A worker owns a run only while it holds the current lease_generation. Every
write it makes goes through fenced(), which re-checks the generation inside
the same transaction, so a slow ("zombie") worker whose lease was taken over
can never write again. All times come from the database clock (now()).
"""

import logging
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.state_machine import append_event, transition

log = logging.getLogger(__name__)


class LostLease(Exception):
    """Another worker owns this run now. Drop it without writing anything."""


@dataclass
class Lease:
    run_id: uuid.UUID
    generation: int
    worker_id: str
    attempts: int
    lost: threading.Event = field(default_factory=threading.Event)

    def check(self) -> None:
        if self.lost.is_set():
            raise LostLease(f"Lease lost for run {self.run_id} (generation {self.generation})")

    @property
    def log_extra(self) -> dict:
        return {
            "run_id": str(self.run_id),
            "worker_id": self.worker_id,
            "generation": self.generation,
        }


# Picks a queued run, or a running one whose lease expired, and leases it in one
# statement. Because the second case is a crashed worker's run, the claim is
# also the recovery trigger: no reconciler process is needed.
CLAIM_SQL = text(
    """
    WITH candidate AS (
        SELECT id, status AS prev_status
        FROM agent_runs
        WHERE (status = 'QUEUED' OR (status = 'RUNNING' AND lease_expires_at < now()))
          AND (CAST(:run_id AS uuid) IS NULL OR id = CAST(:run_id AS uuid))
        ORDER BY created_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    UPDATE agent_runs AS r
    SET lease_owner = :worker,
        lease_generation = r.lease_generation + 1,
        lease_expires_at = now() + make_interval(secs => :lease_seconds),
        status = 'RUNNING',
        attempts = r.attempts + 1
    FROM candidate AS c
    WHERE r.id = c.id
    RETURNING r.id, r.lease_generation, r.attempts, c.prev_status
    """
)

HEARTBEAT_SQL = text(
    """
    UPDATE agent_runs
    SET lease_expires_at = now() + make_interval(secs => :lease_seconds)
    WHERE id = :run_id AND lease_generation = :gen AND status = 'RUNNING'
    """
)

# A no-op write that only matches while we still hold the lease. Running it
# first in a transaction also row-locks the run, so a concurrent claim can't
# slip in between the check and our writes.
FENCE_SQL = text(
    """
    UPDATE agent_runs
    SET lease_owner = lease_owner
    WHERE id = :run_id AND lease_generation = :gen AND status = 'RUNNING'
    """
)


def claim(worker_id: str, run_id: uuid.UUID | None = None) -> Lease | None:
    """
    Lease the next claimable run (or the given one). None means nothing to do.

    A run claimed more than settings.max_attempts times is a poison run: it
    goes to FAILED instead of being handed out again.
    """
    while True:
        with SessionLocal.begin() as session:
            row = session.execute(
                CLAIM_SQL,
                {
                    "worker": worker_id,
                    "lease_seconds": float(settings.lease_seconds),
                    "run_id": str(run_id) if run_id else None,
                },
            ).one_or_none()

            if row is None:
                return None

            lease = Lease(
                run_id=row.id,
                generation=row.lease_generation,
                worker_id=worker_id,
                attempts=row.attempts,
            )

            append_event(
                session,
                lease.run_id,
                "run_claimed",
                {
                    "worker": worker_id,
                    "generation": lease.generation,
                    "attempts": lease.attempts,
                    "from": row.prev_status,
                },
            )

            if lease.attempts <= settings.max_attempts:
                log.info("claimed run", extra={**lease.log_extra, "event": "claimed"})
                return lease

            error = f"POISON_RUN: claimed {lease.attempts} times (max {settings.max_attempts})"
            run = transition(
                session, lease.run_id, "FAILED", event_type="run_failed", payload={"error": error}
            )
            run.error = error
            run.lease_expires_at = None
            log.warning(error, extra={**lease.log_extra, "event": "poison"})

        if run_id is not None:
            return None
        # The poison run is out of the way; look for real work.


def renew(lease: Lease) -> bool:
    """Extend the lease. False means it was lost (taken over or finished)."""
    with SessionLocal.begin() as session:
        result = session.execute(
            HEARTBEAT_SQL,
            {
                "run_id": lease.run_id,
                "gen": lease.generation,
                "lease_seconds": float(settings.lease_seconds),
            },
        )
        return result.rowcount == 1


@contextmanager
def fenced(lease: Lease) -> Iterator[Session]:
    """
    A transaction that commits only if we still own the run.

    Raises LostLease (and rolls back) when the generation has moved on.
    """
    lease.check()

    with SessionLocal.begin() as session:
        result = session.execute(FENCE_SQL, {"run_id": lease.run_id, "gen": lease.generation})

        if result.rowcount != 1:
            lease.lost.set()
            raise LostLease(
                f"Stale generation {lease.generation} for run {lease.run_id}; write rejected"
            )

        yield session


class Heartbeat(threading.Thread):
    """Renews the lease every heartbeat_seconds; flags it lost on a 0-row renew."""

    def __init__(self, lease: Lease, interval: float | None = None):
        super().__init__(daemon=True, name=f"heartbeat-{lease.run_id}")
        self.lease = lease
        self.interval = interval if interval is not None else settings.heartbeat_seconds
        self._stopped = threading.Event()

    def run(self) -> None:
        while not self._stopped.wait(self.interval):
            try:
                ok = renew(self.lease)
            except Exception:
                # A transient DB error: try again next tick. If it persists the
                # lease expires and the fence stops us from writing.
                log.exception("heartbeat failed", extra=self.lease.log_extra)
                continue

            if not ok:
                self.lease.lost.set()
                log.warning("lease lost", extra={**self.lease.log_extra, "event": "lease_lost"})
                return

    def stop(self) -> None:
        self._stopped.set()
