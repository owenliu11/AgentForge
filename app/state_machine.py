# app/state_machine.py
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AgentRun, RunEvent

ALLOWED_TRANSITIONS = {
    "QUEUED": {"RUNNING"},
    "RUNNING": {"COMPLETED", "FAILED"},
    "COMPLETED": set(),
    "FAILED": set(),
}


class IllegalTransition(Exception):
    pass


def _lock_run(session: Session, run_id) -> AgentRun:
    run = session.execute(
        select(AgentRun).where(AgentRun.id == run_id).with_for_update()
    ).scalar_one_or_none()

    if run is None:
        raise ValueError(f"Run {run_id} not found")

    return run


def append_event(
    session: Session,
    run_id,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """
    Append a run event with the next per-run seq.

    Locking the run row serializes writers, so two events can never race
    for the same seq.
    """
    _lock_run(session, run_id)

    next_seq = session.execute(
        select(func.coalesce(func.max(RunEvent.seq), 0) + 1).where(RunEvent.run_id == run_id)
    ).scalar_one()

    session.add(
        RunEvent(
            run_id=run_id,
            seq=next_seq,
            type=event_type,
            payload=payload or {},
        )
    )
    # SessionLocal has autoflush off; flush so a second event in the same
    # transaction sees this one when it computes its seq.
    session.flush()


def transition(
    session: Session,
    run_id,
    to_status: str,
    event_type: str = "transition",
    payload: dict[str, Any] | None = None,
) -> AgentRun:
    """
    Transition a run to a new state and append a run event atomically.

    The caller owns commit/rollback by using a SQLAlchemy session/transaction.
    """
    run = _lock_run(session, run_id)
    from_status = run.status

    if to_status not in ALLOWED_TRANSITIONS.get(from_status, set()):
        raise IllegalTransition(f"Illegal transition: {from_status} -> {to_status}")

    run.status = to_status

    append_event(
        session,
        run_id,
        event_type,
        # from/to go last so a caller's payload can't overwrite them.
        {**(payload or {}), "from": from_status, "to": to_status},
    )

    return run
