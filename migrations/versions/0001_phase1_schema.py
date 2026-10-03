# migrations/versions/0001_phase1_schema.py
"""Phase 1 schema

Revision ID: 0001
Revises:
Create Date: 2026-10-03

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False, server_default=""),
        sa.Column("repo_path", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="QUEUED"),
        sa.Column("lease_owner", sa.Text(), nullable=True),
        sa.Column(
            "lease_generation",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_steps", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED')",
            name="ck_agent_runs_status",
        ),
    )

    op.create_index(
        "ix_agent_runs_status_created_at",
        "agent_runs",
        ["status", "created_at"],
    )

    op.create_table(
        "run_steps",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="DECIDED"),
        sa.Column(
            "decision",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "context",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("run_id", "seq"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('DECIDED', 'SUCCEEDED')",
            name="ck_run_steps_status",
        ),
    )

    op.create_table(
        "tool_calls",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_seq", sa.Integer(), nullable=False),
        sa.Column("tool", sa.Text(), nullable=False),
        sa.Column(
            "args",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="INTENDED"),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "generation",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        # The primary key already makes idempotency_key unique.
        sa.PrimaryKeyConstraint("idempotency_key"),
        sa.CheckConstraint(
            "status IN ('INTENDED', 'SUCCEEDED', 'UNKNOWN')",
            name="ck_tool_calls_status",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "step_seq"],
            ["run_steps.run_id", "run_steps.seq"],
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "run_events",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("run_id", "seq"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    op.drop_table("run_events")
    op.drop_table("tool_calls")
    op.drop_table("run_steps")
    op.drop_index("ix_agent_runs_status_created_at", table_name="agent_runs")
    op.drop_table("agent_runs")
