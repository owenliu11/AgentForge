# app/demo.py
"""
The demo site: run a scenario from the browser instead of several terminals.

Each scenario starts real worker processes, the same ones the kill drills use,
and the page polls this router to show what happens. Only mounted when
DEMO_MODE=true, because it starts processes.
"""

import logging
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.agent import CRASH_POINTS, create_run
from app.config import PROJECT_ROOT, settings
from app.db import SessionLocal
from app.models import AgentRun, RunEvent

log = logging.getLogger(__name__)

PAGE = Path(__file__).parent / "static" / "demo.html"
NOTIFY_SCRIPT = PROJECT_ROOT / "drills" / "scripts" / "notify.yaml"
WORKER_NAMES = ("worker-a", "worker-b", "worker-c", "worker-d")
WORKER_TIMEOUT_SECONDS = 120
# Short leases so a takeover happens in seconds, as in the drills.
DEMO_ENV = {"LEASE_SECONDS": "3", "HEARTBEAT_SECONDS": "1", "POLL_SECONDS": "0.3"}


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    summary: str
    expected: str
    crash_at: tuple[str, ...] = ()
    stall_at: str | None = None
    env: dict[str, str] = field(default_factory=dict)


CRASH_DETAILS = {
    "after_decision": ("the decision is saved", "Worker B replays the saved decision."),
    "after_intent": ("the tool intent is saved", "Worker B re-runs the tool with the same key."),
    "after_effect": (
        "the report is sent, before its result is saved",
        "Worker B re-sends with the same key; 2 deliveries, 1 applied.",
    ),
    "before_commit": (
        "the report is sent, mid-commit",
        "The result is rolled back, so Worker B re-sends; 2 deliveries, 1 applied.",
    ),
}

SCENARIOS = {
    s.key: s
    for s in (
        Scenario(
            key="normal",
            title="Normal run",
            summary="One worker runs the agent from start to finish.",
            expected="COMPLETED with one report applied.",
        ),
        *(
            Scenario(
                key=f"crash_{point}",
                title=f"Crash: {point}",
                summary=f"Worker A dies right after {CRASH_DETAILS[point][0]}.",
                expected=CRASH_DETAILS[point][1],
                crash_at=(point,),
            )
            for point in CRASH_POINTS
        ),
        Scenario(
            key="stale_worker",
            title="Stale worker",
            summary="Worker A freezes after sending the report, then wakes up after a takeover.",
            expected="Worker B finishes. Worker A's late write is rejected.",
            stall_at="after_effect",
        ),
        Scenario(
            key="unknown_effect",
            title="Unknown effect",
            summary="Worker A dies right after a notification that has no idempotency key.",
            expected="FAILED with UNKNOWN_EFFECT. The notification is never re-sent.",
            crash_at=("after_effect",),
            env={"MODEL_SCRIPT": str(NOTIFY_SCRIPT), "AF_CRASH_TOOL": "notify.post"},
        ),
        Scenario(
            key="poison_run",
            title="Poison run",
            summary="Three workers in a row crash on the same run.",
            expected="The fourth claim fails it with POISON_RUN.",
            crash_at=("after_intent",) * 3,
        ),
    )
}


@dataclass
class DemoSession:
    scenario: Scenario
    run_id: uuid.UUID
    workers: list[dict] = field(default_factory=list)
    finished: bool = False
    error: str | None = None
    report: str | None = None


_lock = threading.Lock()
_session: DemoSession | None = None
router = APIRouter(prefix="/demo", include_in_schema=False)


def _start_worker(session: DemoSession, env: dict[str, str]) -> tuple[subprocess.Popen, dict]:
    name = WORKER_NAMES[len(session.workers)]
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("AF_")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.worker", "--worker-id", name, "--run-id", str(session.run_id)],
        cwd=PROJECT_ROOT,
        env={
            **base_env,
            "DATABASE_URL": settings.database_url,
            "MOCK_EFFECTS_URL": settings.mock_effects_url,
            "REPOS_ROOT": str(settings.repos_root),
            "MODEL_SCRIPT": str(settings.model_script),
            **DEMO_ENV,
            **session.scenario.env,
            **env,
        },
    )
    worker = {"name": name, "state": "running"}
    session.workers.append(worker)
    return proc, worker


def _wait(proc: subprocess.Popen, worker: dict) -> None:
    """Wait for a worker to exit and record how it ended."""
    code = proc.wait(timeout=WORKER_TIMEOUT_SECONDS)
    worker["state"] = "crashed" if code == 137 else "finished" if code == 0 else f"exit {code}"


def _wait_until_frozen(proc: subprocess.Popen, worker: dict) -> None:
    deadline = time.monotonic() + WORKER_TIMEOUT_SECONDS

    while time.monotonic() < deadline:
        pid, status = os.waitpid(proc.pid, os.WNOHANG | os.WUNTRACED)

        if pid != 0 and os.WIFSTOPPED(status):
            worker["state"] = "frozen"
            return

        time.sleep(0.05)

    raise TimeoutError("worker-a never froze")


def _load_report(session: DemoSession) -> None:
    with SessionLocal() as db:
        run = db.get(AgentRun, session.run_id)
        reports = (run.result or {}).get("reports") or []

    if reports:
        session.report = httpx.get(reports[0], timeout=5).json()["body"]


def _drive(session: DemoSession) -> None:
    """Runs in a background thread: start each worker the scenario calls for."""
    scenario = session.scenario
    procs = []

    try:
        for point in scenario.crash_at:
            proc, worker = _start_worker(session, {"AF_CRASH_AT": point})
            procs.append(proc)
            _wait(proc, worker)

        zombie = None
        if scenario.stall_at:
            proc, zombie = _start_worker(session, {"AF_STALL_AT": scenario.stall_at})
            procs.append(proc)
            _wait_until_frozen(proc, zombie)

        survivor_proc, survivor = _start_worker(session, {})
        procs.append(survivor_proc)
        _wait(survivor_proc, survivor)

        if zombie is not None:
            zombie["state"] = "woken up"
            os.kill(procs[0].pid, signal.SIGCONT)
            procs[0].wait(timeout=WORKER_TIMEOUT_SECONDS)
            zombie["state"] = "fenced off"

        _load_report(session)
    except Exception as exc:
        log.exception("demo scenario failed")
        session.error = str(exc)
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        session.finished = True


def start_scenario(key: str) -> DemoSession:
    """Start a scenario in the background. Raises if one is already running."""
    global _session
    scenario = SCENARIOS.get(key)

    if scenario is None:
        raise HTTPException(status_code=404, detail="Unknown scenario")

    with _lock:
        if _session is not None and not _session.finished:
            raise HTTPException(status_code=409, detail="A scenario is already running")

        try:
            # Counters on the page should describe this run only.
            url = f"{settings.mock_effects_url.rstrip('/')}/reset"
            httpx.post(url, timeout=5).raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=503, detail="mock_effects isn't running. Restart make demo."
            ) from exc

        try:
            run_id = create_run(objective=scenario.title, repo_path="sample_repo")
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=503, detail="The database isn't reachable. Run make up."
            ) from exc

        _session = DemoSession(scenario=scenario, run_id=run_id)
        threading.Thread(target=_drive, args=(_session,), daemon=True).start()
        return _session


def _effects() -> dict | None:
    try:
        return httpx.get(f"{settings.mock_effects_url.rstrip('/')}/stats", timeout=2).json()
    except httpx.HTTPError:
        return None


def session_state(session: DemoSession) -> dict:
    with SessionLocal() as db:
        run = db.get(AgentRun, session.run_id)
        events = db.scalars(
            select(RunEvent).where(RunEvent.run_id == session.run_id).order_by(RunEvent.seq)
        ).all()
        # The database clock decides when a lease expires, so read it from there.
        lease_remaining = db.scalar(
            text(
                "SELECT EXTRACT(EPOCH FROM lease_expires_at - now()) FROM agent_runs WHERE id=:id"
            ),
            {"id": session.run_id},
        )

    return {
        "scenario": session.scenario.key,
        "finished": session.finished,
        "error": session.error,
        "report": session.report,
        "workers": session.workers,
        "effects": _effects(),
        "run": {
            "id": str(run.id),
            "status": run.status,
            "lease_owner": run.lease_owner,
            "lease_generation": run.lease_generation,
            "lease_remaining": None if lease_remaining is None else float(lease_remaining),
            "attempts": run.attempts,
            "error": run.error,
        },
        "events": [{"seq": e.seq, "type": e.type, "payload": e.payload} for e in events],
    }


@router.get("")
def demo_page() -> FileResponse:
    return FileResponse(PAGE, media_type="text/html")


@router.get("/")
def demo_redirect() -> RedirectResponse:
    return RedirectResponse("/demo")


@router.get("/api/scenarios")
def list_scenarios() -> list[dict]:
    return [
        {"key": s.key, "title": s.title, "summary": s.summary, "expected": s.expected}
        for s in SCENARIOS.values()
    ]


@router.post("/api/scenarios/{key}", status_code=202)
def run_scenario(key: str) -> dict:
    session = start_scenario(key)
    return {"run_id": str(session.run_id)}


@router.get("/api/state")
def get_state() -> dict | None:
    return session_state(_session) if _session is not None else None
