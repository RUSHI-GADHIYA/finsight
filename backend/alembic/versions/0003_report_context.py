"""reports.context: cited passages and tables stored with each approved report

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("reports", sa.Column("context", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("reports", "context")
