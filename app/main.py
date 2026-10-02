# app/main.py
import uuid
from contextlib import asynccontextmanager

import psycopg
import psycopg.rows
from fastapi import BackgroundTasks, FastAPI
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from app.agent import run_agent_loop
from app.config import settings
from app.db import init_db, ping_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize DB schema
    init_db()
    yield


app = FastAPI(title="AgentForge", version="0.1.0", lifespan=lifespan)


class RunRequest(BaseModel):
    payload: dict = {}


@app.get("/health")
def health() -> JSONResponse:
    db_up = ping_db()
    status = "ok" if db_up else "degraded"
    db_status = "up" if db_up else "down"
    status_code = 200 if db_up else 503

    return JSONResponse(
        status_code=status_code,
        content={"status": status, "db": db_status},
    )


@app.post("/runs")
def create_run(req: RunRequest, background_tasks: BackgroundTasks):
    # 1. Create the run record
    run_id = str(uuid.uuid4())

    # Use settings.database_url and wrap the payload in Jsonb
    with psycopg.connect(settings.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_runs (id, payload) VALUES (%s, %s)", (run_id, Jsonb(req.payload))
            )
        conn.commit()

    # 2. Kick off the agent loop in the background
    background_tasks.add_task(run_agent_loop, run_id, req.payload)

    return {"run_id": run_id, "status": "pending"}


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    with psycopg.connect(settings.database_url) as conn:
        # Use dict_row so psycopg returns a dictionary instead of a tuple
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                """SELECT id, status, payload, result, created_at, updated_at 
                   FROM agent_runs WHERE id = %s""",
                (run_id,),
            )
            row = cur.fetchone()

            if not row:
                return JSONResponse(status_code=404, content={"error": "Run not found"})

            return row
