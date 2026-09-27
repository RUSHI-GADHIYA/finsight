"""reports approved at the end of an agent run

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-26

LangGraph's checkpoint tables are not managed here: AsyncPostgresSaver.setup() creates and
migrates its own tables at app startup.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reports",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("thread_id", sa.String(64), nullable=False, unique=True),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("report", JSONB, nullable=False),
        sa.Column("warnings", JSONB, nullable=False),
        sa.Column("cost_usd", sa.Float, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("reports")
