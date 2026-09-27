"""filings and chunks with pgvector + full-text search

Revision ID: 0001
Revises:
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import TSVECTOR

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "filings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticker", sa.String(12), nullable=False, index=True),
        sa.Column("cik", sa.String(10), nullable=False),
        sa.Column("form", sa.String(10), nullable=False),
        sa.Column("accession_no", sa.String(25), nullable=False, unique=True),
        sa.Column("filing_date", sa.Date, nullable=False),
        sa.Column("report_date", sa.Date),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column(
            "ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "filing_id",
            sa.Integer,
            sa.ForeignKey("filings.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("section", sa.String(64), nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column("tsv", TSVECTOR, sa.Computed("to_tsvector('english', text)", persisted=True)),
    )
    op.create_index("ix_chunks_tsv", "chunks", ["tsv"], postgresql_using="gin")
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_table("chunks")
    op.drop_table("filings")
