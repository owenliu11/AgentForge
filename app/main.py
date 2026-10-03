# app/main.py
import uuid
from datetime import datetime

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agent import create_run, run_agent
from app.db import SessionLocal, ping_db
from app.models import AgentRun, RunEvent

app = FastAPI(
    title="AgentForge",
    version="0.1.0",
)


class RunRequest(BaseModel):
    objective: str = ""
    # Relative to settings.repos_root.
    repo_path: str = "sample_repo"
    max_steps: int = Field(default=10, ge=1, le=50)


def format_dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def serialize_run(run: AgentRun) -> dict:
    return {
        "id": str(run.id),
        "objective": run.objective,
        "repo_path": run.repo_path,
        "status": run.status,
        "lease_owner": run.lease_owner,
        "lease_generation": run.lease_generation,
        "lease_expires_at": format_dt(run.lease_expires_at),
        "attempts": run.attempts,
        "max_steps": run.max_steps,
        "result": run.result,
        "error": run.error,
        "created_at": format_dt(run.created_at),
    }


@app.get("/health")
def health() -> JSONResponse:
    db_up = ping_db()

    status = "ok" if db_up else "degraded"
    db_status = "up" if db_up else "down"
    status_code = 200 if db_up else 503

    return JSONResponse(
        status_code=status_code,
        content={
            "status": status,
            "db": db_status,
        },
    )


@app.post("/runs", status_code=202)
def post_run(req: RunRequest, background_tasks: BackgroundTasks):
    try:
        run_id = create_run(
            objective=req.objective,
            repo_path=req.repo_path,
            max_steps=req.max_steps,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Phase 1: run in-process after the response is sent.
    # Phase 2 replaces this with workers claiming QUEUED runs.
    background_tasks.add_task(run_agent, run_id)

    return {"id": str(run_id), "status": "QUEUED"}


@app.get("/runs/{run_id}")
def get_run(run_id: uuid.UUID):
    with SessionLocal() as session:
        run = session.get(AgentRun, run_id)

        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")

        return serialize_run(run)


@app.get("/runs/{run_id}/events")
def get_run_events(run_id: uuid.UUID):
    with SessionLocal() as session:
        if session.get(AgentRun, run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")

        events = (
            session.execute(
                select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)
            )
            .scalars()
            .all()
        )

        return [
            {
                "seq": event.seq,
                "type": event.type,
                "payload": event.payload,
                "created_at": format_dt(event.created_at),
            }
            for event in events
        ]
