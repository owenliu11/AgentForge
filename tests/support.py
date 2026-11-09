# tests/support.py
"""Fixtures and helpers shared by tests/ and drills/."""

import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url

from app.config import settings
from app.db import SessionLocal, run_migrations
from app.models import AgentRun, RunEvent, RunStep, ToolCall

ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {"COMPLETED", "FAILED"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(predicate, timeout: float = 30.0, interval: float = 0.1, what: str = "condition"):
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)

    raise AssertionError(f"Timed out after {timeout}s waiting for {what}")


def require_test_database(url: str, allow_reset: str | None) -> None:
    """Refuse destructive fixtures against a normal development/cloud database."""
    database = make_url(url).database or ""
    if allow_reset != "1" or not database.endswith("_test"):
        raise RuntimeError(
            "Destructive tests require a database ending in _test and "
            "AGENTFORGE_ALLOW_TEST_RESET=1. Use make test or make drill."
        )


@pytest.fixture(scope="session")
def migrated_db():
    require_test_database(
        settings.sqlalchemy_database_url, os.environ.get("AGENTFORGE_ALLOW_TEST_RESET")
    )
    run_migrations()


@pytest.fixture(scope="session")
def mock_effects_url():
    """One mock_effects server shared by the test process and worker subprocesses."""
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "mock_effects.app:app",
            "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning",
        ],
        cwd=ROOT,
    )  # fmt: skip

    def up():
        try:
            return httpx.get(f"{url}/stats", timeout=1).status_code == 200
        except httpx.HTTPError:
            return False

    try:
        wait_for(up, timeout=15, what="mock_effects to start")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture
def db(migrated_db, mock_effects_url, tmp_path, monkeypatch):
    """Empty tables and effects, and keep reports out of the project dir."""
    with SessionLocal.begin() as session:
        session.execute(text("TRUNCATE TABLE run_events, tool_calls, run_steps, agent_runs"))

    httpx.post(f"{mock_effects_url}/reset", timeout=5).raise_for_status()
    monkeypatch.setattr(settings, "reports_dir", tmp_path / "reports")
    monkeypatch.setattr(settings, "mock_effects_url", mock_effects_url)


def effect_stats() -> dict:
    return httpx.get(f"{settings.mock_effects_url}/stats", timeout=5).json()


def load_run(run_id):
    with SessionLocal() as session:
        run = session.get(AgentRun, run_id)
        steps = session.scalars(
            select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.seq)
        ).all()
        calls = session.scalars(
            select(ToolCall).where(ToolCall.run_id == run_id).order_by(ToolCall.step_seq)
        ).all()
        events = session.scalars(
            select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)
        ).all()
    return run, steps, calls, events


def run_status(run_id) -> str:
    with SessionLocal() as session:
        return session.get(AgentRun, run_id).status


def count_status(status: str) -> int:
    with SessionLocal() as session:
        return session.scalar(
            select(func.count()).select_from(AgentRun).where(AgentRun.status == status)
        )


def expire_lease(run_id) -> None:
    """Pretend the owner's lease ran out, without waiting for it."""
    with SessionLocal.begin() as session:
        session.execute(
            text(
                "UPDATE agent_runs SET lease_expires_at = now() - interval '1 second' "
                "WHERE id = :id"
            ),
            {"id": run_id},
        )


class Worker:
    """A worker running as a real OS process, so it can be killed or frozen."""

    def __init__(self, proc: subprocess.Popen, worker_id: str, log_path: Path):
        self.proc = proc
        self.worker_id = worker_id
        self.log_path = log_path

    def log(self) -> str:
        return self.log_path.read_text(encoding="utf-8") if self.log_path.exists() else ""

    def wait(self, timeout: float = 60) -> int:
        return self.proc.wait(timeout=timeout)

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()


def start_worker(
    log_dir: Path,
    worker_id: str | None = None,
    env: dict[str, str] | None = None,
    exit_when_idle: bool = False,
) -> Worker:
    worker_id = worker_id or f"w-{uuid.uuid4().hex[:6]}"
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("AF_")}
    full_env = {
        **base_env,
        "DATABASE_URL": settings.database_url,
        "MOCK_EFFECTS_URL": settings.mock_effects_url,
        "REPOS_ROOT": str(settings.repos_root),
        "MODEL_SCRIPT": str(settings.model_script),
        "SANDBOX_IMAGE": settings.sandbox_image,
        # Short leases keep the drills fast.
        "LEASE_SECONDS": "2",
        "HEARTBEAT_SECONDS": "0.5",
        "POLL_SECONDS": "0.2",
        **(env or {}),
    }
    log_path = log_dir / f"{worker_id}.log"
    cmd = [sys.executable, "-m", "app.worker", "--worker-id", worker_id]

    if exit_when_idle:
        cmd.append("--exit-when-idle")

    with log_path.open("w") as log_file:
        proc = subprocess.Popen(
            cmd, cwd=ROOT, env=full_env, stdout=log_file, stderr=subprocess.STDOUT
        )

    return Worker(proc, worker_id, log_path)
