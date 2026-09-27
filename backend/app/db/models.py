from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBEDDING_DIM = 384  # must match settings.embedding_dim and the alembic migration


class Base(DeclarativeBase):
    pass


class Filing(Base):
    __tablename__ = "filings"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(12), index=True)
    cik: Mapped[str] = mapped_column(String(10))
    form: Mapped[str] = mapped_column(String(10))
    accession_no: Mapped[str] = mapped_column(String(25), unique=True)
    filing_date: Mapped[date] = mapped_column(Date)
    report_date: Mapped[date | None] = mapped_column(Date)
    url: Mapped[str] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    chunks: Mapped[list["Chunk"]] = relationship(back_populates="filing", cascade="all, delete")


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    filing_id: Mapped[int] = mapped_column(ForeignKey("filings.id", ondelete="CASCADE"), index=True)
    section: Mapped[str] = mapped_column(String(64))
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    # Generated column powering the BM25-style half of hybrid search.
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', text)", persisted=True)
    )

    filing: Mapped[Filing] = relationship(back_populates="chunks")

    __table_args__ = (
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class SavedReport(Base):
    """A research brief a human approved (or edited) at the end of an agent run."""

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(64), unique=True)  # LangGraph thread
    question: Mapped[str] = mapped_column(Text)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB)  # app.agents.state.Report
    warnings: Mapped[list[str]] = mapped_column(JSONB)
    cost_usd: Mapped[float] = mapped_column(Float)
    # Cited passages + tables behind the figures (app.agents.state.ReportContext).
    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEvent(Base):
    """Security- and review-relevant events: blocked inputs, dropped passages, sign-offs."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    thread_id: Mapped[str | None] = mapped_column(String(64), index=True)
    event: Mapped[str] = mapped_column(String(40), index=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ResearchRun(Base):
    """One research run's cost and latency (the /metrics page reads these)."""

    __tablename__ = "research_runs"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24))
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)  # until the review pause / end
    node_timings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
