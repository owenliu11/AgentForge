# app/models.py
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    objective: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
    )

    # Relative to settings.repos_root, so any worker host can resolve it.
    repo_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="QUEUED",
    )

    lease_owner: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    lease_generation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    max_steps: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=10,
    )

    result: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED')",
            name="ck_agent_runs_status",
        ),
    )


class RunStep(Base):
    __tablename__ = "run_steps"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )

    seq: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="DECIDED",
    )

    decision: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    context: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('DECIDED', 'SUCCEEDED')",
            name="ck_run_steps_status",
        ),
    )


class ToolCall(Base):
    __tablename__ = "tool_calls"

    idempotency_key: Mapped[str] = mapped_column(
        Text,
        primary_key=True,
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )

    step_seq: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    tool: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    args: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="INTENDED",
    )

    result: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    generation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "step_seq"],
            ["run_steps.run_id", "run_steps.seq"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('INTENDED', 'SUCCEEDED', 'UNKNOWN')",
            name="ck_tool_calls_status",
        ),
    )


class RunEvent(Base):
    __tablename__ = "run_events"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )

    seq: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    payload: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
