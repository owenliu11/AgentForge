# app/worker.py
"""
Worker process: claim a run, execute it, repeat.

Workers coordinate only through Postgres, so you can run as many as you like:

    uv run python -m app.worker
    uv run python -m app.worker --worker-id w2 --exit-when-idle
"""

import argparse
import logging
import os
import socket
import time
import uuid

import httpx

from app.agent import execute_run
from app.config import settings
from app.db import SessionLocal
from app.leases import claim
from app.logs import configure_logging
from app.model_interface import Model
from app.models import AgentRun

log = logging.getLogger(__name__)


def default_worker_id() -> str:
    """
    A unique id. On ECS it's the task id, so `aws ecs stop-task` can target the
    owner of a run. Containers sharing a host all have PID 1, so a random suffix
    keeps the fallback unique.
    """
    metadata_uri = os.environ.get("ECS_CONTAINER_METADATA_URI_V4")

    if metadata_uri:
        try:
            task_arn = httpx.get(f"{metadata_uri}/task", timeout=5).json()["TaskARN"]
            return task_arn.rsplit("/", 1)[-1]
        except (httpx.HTTPError, KeyError, ValueError):
            log.warning("no ECS task metadata; using a generated worker id")

    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"


def drain(worker_id: str, model: Model | None = None, run_id: uuid.UUID | None = None) -> int:
    """Process claimable runs (or just run_id) until none are left. Returns how many it ran."""
    count = 0

    while (lease := claim(worker_id, run_id=run_id)) is not None:
        execute_run(lease, model)
        count += 1

    return count


def run_finished(run_id: uuid.UUID) -> bool:
    with SessionLocal() as session:
        return session.get(AgentRun, run_id).status in ("COMPLETED", "FAILED")


def work_forever(
    worker_id: str,
    exit_when_idle: bool = False,
    run_id: uuid.UUID | None = None,
) -> None:
    log.info("worker started", extra={"worker_id": worker_id, "event": "started"})

    while True:
        try:
            ran = drain(worker_id, run_id=run_id)

            if run_id is not None and run_finished(run_id):
                return
        except Exception:
            # Usually the database is down or restarting. Don't die: any run we
            # held keeps its lease until it expires, then the claim recovers it.
            log.exception("worker loop error", extra={"worker_id": worker_id, "event": "error"})
            time.sleep(settings.poll_seconds * 5)
            continue

        if ran == 0 and exit_when_idle:
            log.info("worker idle; exiting", extra={"worker_id": worker_id, "event": "idle"})
            return

        if ran == 0:
            time.sleep(settings.poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentForge worker")
    parser.add_argument("--worker-id", default=None)
    parser.add_argument(
        "--exit-when-idle",
        action="store_true",
        help="Exit once no run is claimable (tests and batch jobs)",
    )
    parser.add_argument(
        "--run-id",
        type=uuid.UUID,
        default=None,
        help="Work only on this run and exit when it finishes (the demo uses this)",
    )
    args = parser.parse_args()

    configure_logging()
    work_forever(
        args.worker_id or default_worker_id(),
        exit_when_idle=args.exit_when_idle,
        run_id=args.run_id,
    )


if __name__ == "__main__":
    main()
