"""audit_log and research_runs for guardrails, sign-offs and the metrics page

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("thread_id", sa.String(64)),
        sa.Column("event", sa.String(40), nullable=False),
        sa.Column("detail", JSONB, nullable=False),
    )
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])
    op.create_index("ix_audit_log_thread_id", "audit_log", ["thread_id"])
    op.create_index("ix_audit_log_event", "audit_log", ["event"])

    op.create_table(
        "research_runs",
        sa.Column("thread_id", sa.String(64), primary_key=True),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("cost_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("node_timings", JSONB, nullable=False, server_default="{}"),
        sa.Column("cache_hit", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_research_runs_created_at", "research_runs", ["created_at"])


def downgrade() -> None:
    op.drop_table("research_runs")
    op.drop_table("audit_log")
