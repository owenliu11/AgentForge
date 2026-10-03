# app/agent.py
import hashlib
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from app.config import settings
from app.db import SessionLocal
from app.model_interface import FakeModel, Model
from app.models import AgentRun, RunStep, ToolCall
from app.state_machine import append_event, transition
from app.tools import ToolContext, ToolError, execute_tool

log = logging.getLogger(__name__)


class RunFailed(Exception):
    pass


def canonical_json(obj: dict[str, Any]) -> str:
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def make_idempotency_key(
    run_id: uuid.UUID,
    step_seq: int,
    tool: str,
    args: dict[str, Any],
) -> str:
    raw = f"{run_id}|{step_seq}|{tool}|{canonical_json(args)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_next_context(
    tool: str,
    result: dict[str, Any],
    seq: int,
    previous_context: dict[str, Any],
) -> dict[str, Any]:
    reports = previous_context.get("reports", [])

    if tool == "report.publish" and result.get("report_path"):
        reports = reports + [result["report_path"]]

    return {
        "last_tool": tool,
        "last_step_seq": seq,
        "last_tool_result": result,
        "reports": reports,
    }


def resolve_repo_path(repo_path: str) -> Path:
    """Resolve a run's repo_path, which must stay inside settings.repos_root."""
    root = Path(settings.repos_root).resolve()
    candidate = (root / repo_path).resolve()

    if not candidate.is_relative_to(root) or not candidate.is_dir():
        raise ValueError(f"Repo not found under repos_root: {repo_path}")

    return candidate


def create_run(objective: str, repo_path: str, max_steps: int = 10) -> uuid.UUID:
    resolve_repo_path(repo_path)

    with SessionLocal.begin() as session:
        run = AgentRun(
            objective=objective,
            repo_path=repo_path,
            status="QUEUED",
            max_steps=max_steps,
        )
        session.add(run)
        session.flush()
        append_event(session, run.id, "run_created", {"objective": objective})
        return run.id


def get_model() -> Model:
    return FakeModel(settings.model_script)


def run_agent(run_id: uuid.UUID, model: Model | None = None) -> str:
    """
    Run one agent loop to completion and return the final run status.

    Every step commits in decision-first order, one transaction per write:
    (1) decision, (2) tool intent with its key, (3) run the tool,
    (4) result plus checkpoint.
    """
    model = model or get_model()

    with SessionLocal.begin() as session:
        run = transition(session, run_id, "RUNNING", event_type="run_started")
        repo_path_value = run.repo_path
        max_steps = run.max_steps
        generation = run.lease_generation

    reports_dir = Path(settings.reports_dir).resolve() / str(run_id)
    context: dict[str, Any] = {}

    try:
        repo_path = resolve_repo_path(repo_path_value)

        for seq in range(1, max_steps + 1):
            decision = model.decide(seq, context)

            # 1. Save the decision before acting.
            with SessionLocal.begin() as session:
                session.add(
                    RunStep(
                        run_id=run_id,
                        seq=seq,
                        status="DECIDED",
                        decision=decision,
                        context=context,
                    )
                )
                session.flush()
                append_event(session, run_id, "step_decided", {"seq": seq, **decision})

            if decision["action"] == "finish":
                result = {**decision.get("result", {}), "reports": context.get("reports", [])}

                with SessionLocal.begin() as session:
                    step = session.get(RunStep, (run_id, seq))
                    step.status = "SUCCEEDED"

                    run = transition(session, run_id, "COMPLETED", event_type="run_completed")
                    run.result = result

                return "COMPLETED"

            if decision["action"] != "tool_call":
                raise RunFailed(f"Unknown decision action: {decision['action']}")

            tool = decision["tool"]
            args = decision.get("args", {})
            idempotency_key = make_idempotency_key(run_id, seq, tool, args)

            # 2. Save the tool intent before running the tool.
            with SessionLocal.begin() as session:
                session.add(
                    ToolCall(
                        idempotency_key=idempotency_key,
                        run_id=run_id,
                        step_seq=seq,
                        tool=tool,
                        args=args,
                        status="INTENDED",
                        generation=generation,
                    )
                )
                session.flush()
                append_event(
                    session,
                    run_id,
                    "tool_intended",
                    {"seq": seq, "tool": tool, "idempotency_key": idempotency_key},
                )

            # 3. Run the tool. A rejected call (bad path, unknown tool, ...)
            # still completed; the error is the observation the model sees.
            try:
                result = execute_tool(
                    tool,
                    args,
                    ToolContext(
                        repo_path=repo_path,
                        reports_dir=reports_dir,
                        context=context,
                        idempotency_key=idempotency_key,
                    ),
                )
            except ToolError as exc:
                result = {"error": str(exc)}

            context = build_next_context(tool, result, seq, context)

            # 4. Save result and checkpoint.
            with SessionLocal.begin() as session:
                call = session.get(ToolCall, idempotency_key)
                call.status = "SUCCEEDED"
                call.result = result

                step = session.get(RunStep, (run_id, seq))
                step.status = "SUCCEEDED"
                step.context = context

                append_event(
                    session,
                    run_id,
                    "tool_succeeded",
                    {"seq": seq, "tool": tool, "error": result.get("error")},
                )

        raise RunFailed(f"MAX_STEPS_EXCEEDED: no finish within {max_steps} steps")

    except Exception as exc:
        log.exception("Run %s failed", run_id)

        with SessionLocal.begin() as session:
            run = transition(
                session,
                run_id,
                "FAILED",
                event_type="run_failed",
                payload={"error": str(exc)},
            )
            run.error = str(exc)

        return "FAILED"
