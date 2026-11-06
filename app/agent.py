# app/agent.py
import hashlib
import json
import logging
import os
import signal
import uuid
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.exc import InterfaceError, OperationalError

from app.config import settings
from app.db import SessionLocal
from app.leases import Heartbeat, Lease, LostLease, claim, fenced
from app.model_interface import FakeModel, Model
from app.models import AgentRun, RunStep, ToolCall
from app.state_machine import append_event, transition
from app.tools import TOOL_EFFECTS, ToolContext, ToolError, execute_tool

log = logging.getLogger(__name__)

CRASH_POINTS = ("after_decision", "after_intent", "after_effect", "before_commit")


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

    if tool == "report.publish" and result.get("report_url"):
        reports = reports + [result["report_url"]]

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


def fault_point(point: str, tool: str | None, lease: Lease) -> None:
    """
    Kill drills: crash (AF_CRASH_AT) or freeze (AF_STALL_AT) at a named line.

    Only fires on the step that calls AF_CRASH_TOOL (default report.publish).
    os._exit skips every cleanup, like a SIGKILL; SIGSTOP freezes the whole
    process, heartbeat included, like a long GC pause or a hung VM.
    """
    if tool is None or tool != os.environ.get("AF_CRASH_TOOL", "report.publish"):
        return

    if os.environ.get("AF_CRASH_AT") == point:
        log.warning("crash point", extra={**lease.log_extra, "crash_at": point, "event": "crash"})
        os._exit(137)

    if os.environ.get("AF_STALL_AT") == point:
        log.warning("stall point", extra={**lease.log_extra, "crash_at": point, "event": "stall"})
        os.kill(os.getpid(), signal.SIGSTOP)


def execute_run(lease: Lease, model: Model | None = None) -> str:
    """
    Run (or resume) a leased run. Returns COMPLETED, FAILED, LOST, or ABANDONED.

    LOST means another worker took the run over; we dropped it without writing.
    ABANDONED means a transient infrastructure error; the lease expires and the
    run is claimed again.
    """
    heartbeat = Heartbeat(lease)
    heartbeat.start()

    try:
        return _execute(lease, model or get_model())
    except LostLease as exc:
        log.warning(str(exc), extra={**lease.log_extra, "event": "lease_lost"})
        return "LOST"
    finally:
        heartbeat.stop()


def _load(run_id: uuid.UUID) -> tuple[AgentRun, list[RunStep], dict[int, ToolCall]]:
    with SessionLocal() as session:
        run = session.get(AgentRun, run_id)
        steps = session.scalars(
            select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.seq)
        ).all()
        calls = session.scalars(select(ToolCall).where(ToolCall.run_id == run_id)).all()
    return run, list(steps), {c.step_seq: c for c in calls}


def _execute(lease: Lease, model: Model) -> str:
    """
    The agent loop, in decision-first order, one fenced transaction per write:
    (1) decision, (2) tool intent with its key, (3) run the tool,
    (4) result plus checkpoint.

    On a claim it resumes at the first missing record:
      no decision saved          -> ask the model again
      decision, no intent        -> replay the saved decision
      intent, no result, retryable or keyed tool -> re-run with the same key
      intent, no result, unkeyed effect          -> fail with UNKNOWN_EFFECT
      result saved               -> continue with the next step
    """
    run_id = lease.run_id
    extra = lease.log_extra
    run, steps, calls = _load(run_id)
    max_steps = run.max_steps
    reports_dir = Path(settings.reports_dir).resolve() / str(run_id)

    context: dict[str, Any] = {}
    seq = 1
    replay: RunStep | None = None

    if steps:
        last = steps[-1]
        context = last.context or {}

        if last.status == "SUCCEEDED":
            seq = last.seq + 1
        else:
            seq = last.seq
            replay = last

        with fenced(lease) as session:
            append_event(
                session,
                run_id,
                "run_resumed",
                {
                    "seq": seq,
                    "generation": lease.generation,
                    "replayed_decision": replay is not None,
                    "intent_found": replay is not None and seq in calls,
                },
            )
        log.info("resuming run", extra={**extra, "seq": seq, "event": "resumed"})

    try:
        repo_path = resolve_repo_path(run.repo_path)

        while seq <= max_steps:
            lease.check()
            existing_call: ToolCall | None = None

            if replay is not None:
                # 1'. Replay the saved decision; don't re-ask the model.
                decision = replay.decision
                existing_call = calls.get(seq)
                replay = None
            else:
                decision = model.decide(seq, context)

                # 1. Save the decision before acting.
                with fenced(lease) as session:
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

            tool = decision.get("tool")
            fault_point("after_decision", tool, lease)

            if decision["action"] == "finish":
                result = {**decision.get("result", {}), "reports": context.get("reports", [])}

                with fenced(lease) as session:
                    session.get(RunStep, (run_id, seq)).status = "SUCCEEDED"
                    run = transition(session, run_id, "COMPLETED", event_type="run_completed")
                    run.result = result
                    run.lease_expires_at = None

                log.info("run completed", extra={**extra, "event": "completed"})
                return "COMPLETED"

            if decision["action"] != "tool_call":
                raise RunFailed(f"Unknown decision action: {decision['action']}")

            args = decision.get("args", {})
            idempotency_key = make_idempotency_key(run_id, seq, tool, args)
            effect = TOOL_EFFECTS.get(tool, "none")

            if existing_call is None:
                # 2. Save the tool intent before running the tool.
                with fenced(lease) as session:
                    session.add(
                        ToolCall(
                            idempotency_key=idempotency_key,
                            run_id=run_id,
                            step_seq=seq,
                            tool=tool,
                            args=args,
                            status="INTENDED",
                            generation=lease.generation,
                        )
                    )
                    session.flush()
                    append_event(
                        session,
                        run_id,
                        "tool_intended",
                        {"seq": seq, "tool": tool, "idempotency_key": idempotency_key},
                    )
            elif effect == "unkeyed" or existing_call.status == "UNKNOWN":
                # 2'. A side effect that can't be deduplicated may or may not
                # have happened. Retrying could do it twice, so stop loudly.
                error = f"UNKNOWN_EFFECT: {tool} at step {seq} may or may not have run"

                with fenced(lease) as session:
                    call = session.get(ToolCall, idempotency_key)
                    call.status = "UNKNOWN"
                    call.generation = lease.generation
                    append_event(session, run_id, "tool_unknown", {"seq": seq, "tool": tool})
                    run = transition(
                        session,
                        run_id,
                        "FAILED",
                        event_type="run_failed",
                        payload={"error": error},
                    )
                    run.error = error
                    run.lease_expires_at = None

                log.warning(error, extra={**extra, "seq": seq, "tool": tool, "event": "failed"})
                return "FAILED"
            else:
                # 2'. Read-only or idempotency-keyed: safe to run again with
                # the same key; the downstream dedupes a repeated effect.
                with fenced(lease) as session:
                    session.get(ToolCall, idempotency_key).generation = lease.generation
                    append_event(
                        session,
                        run_id,
                        "tool_retried",
                        {"seq": seq, "tool": tool, "idempotency_key": idempotency_key},
                    )

            fault_point("after_intent", tool, lease)
            lease.check()

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

            fault_point("after_effect", tool, lease)
            context = build_next_context(tool, result, seq, context)

            # 4. Save result and checkpoint.
            with fenced(lease) as session:
                call = session.get(ToolCall, idempotency_key)
                call.status = "SUCCEEDED"
                call.result = result
                call.generation = lease.generation

                step = session.get(RunStep, (run_id, seq))
                step.status = "SUCCEEDED"
                step.context = context

                append_event(
                    session,
                    run_id,
                    "tool_succeeded",
                    {"seq": seq, "tool": tool, "error": result.get("error")},
                )
                session.flush()
                fault_point("before_commit", tool, lease)

            seq += 1

        raise RunFailed(f"MAX_STEPS_EXCEEDED: no finish within {max_steps} steps")

    except LostLease:
        raise

    except (OperationalError, InterfaceError, httpx.TransportError, httpx.HTTPStatusError) as exc:
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
            return _fail(lease, exc)

        # Infrastructure, not the run: the database or a downstream is briefly
        # unavailable. Drop the run without failing it; the lease expires and a
        # claim retries it (backed off by the lease length, capped by attempts).
        # An unkeyed effect whose outcome is unknown fails as UNKNOWN_EFFECT then.
        log.warning(
            "transient error; abandoning run for retry",
            exc_info=True,
            extra={**extra, "event": "abandoned"},
        )
        return "ABANDONED"

    except Exception as exc:
        return _fail(lease, exc)


def _fail(lease: Lease, exc: Exception) -> str:
    """Fail the run permanently, recording the error."""
    run_id = lease.run_id
    log.error("run failed", exc_info=exc, extra={**lease.log_extra, "event": "failed"})

    with fenced(lease) as session:
        run = transition(
            session,
            run_id,
            "FAILED",
            event_type="run_failed",
            payload={"error": str(exc)},
        )
        run.error = str(exc)
        run.lease_expires_at = None

    return "FAILED"


def run_agent(run_id: uuid.UUID, model: Model | None = None, worker_id: str = "inline") -> str:
    """Claim one specific run and execute it in this process (tests and demos)."""
    lease = claim(worker_id, run_id=run_id)

    if lease is None:
        raise ValueError(f"Run {run_id} is not claimable")

    return execute_run(lease, model)
