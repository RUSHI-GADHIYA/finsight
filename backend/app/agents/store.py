"""Approved reports in Postgres (the `reports` table)."""

from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import select

from app.agents.state import Report, ReportContext
from app.db.models import SavedReport
from app.db.session import get_sessionmaker


class ReportOut(BaseModel):
    id: int
    thread_id: str
    question: str
    report: Report
    warnings: list[str]
    cost_usd: float
    context: ReportContext | None  # None for reports saved before migration 0003
    created_at: datetime


def _out(row: SavedReport) -> ReportOut:
    return ReportOut(
        id=row.id,
        thread_id=row.thread_id,
        question=row.question,
        report=Report.model_validate(row.report),
        warnings=row.warnings,
        cost_usd=row.cost_usd,
        context=ReportContext.model_validate(row.context) if row.context else None,
        created_at=row.created_at,
    )


class PostgresReportStore:
    async def save(
        self,
        *,
        thread_id: str,
        question: str,
        report: Report,
        warnings: list[str],
        cost_usd: float,
        context: ReportContext,
    ) -> int:
        async with get_sessionmaker()() as session:
            # A resumed node re-runs from the top; if a retry lands after the insert, reuse it.
            existing = await session.scalar(
                select(SavedReport).where(SavedReport.thread_id == thread_id)
            )
            if existing is not None:
                return existing.id
            row = SavedReport(
                thread_id=thread_id,
                question=question,
                report=report.model_dump(mode="json"),
                warnings=warnings,
                cost_usd=cost_usd,
                context=context.model_dump(mode="json"),
            )
            session.add(row)
            await session.commit()
            return row.id

    async def get(self, report_id: int) -> ReportOut | None:
        async with get_sessionmaker()() as session:
            row = await session.get(SavedReport, report_id)
            return _out(row) if row else None

    async def recent(self, limit: int = 50) -> list[ReportOut]:
        async with get_sessionmaker()() as session:
            rows = await session.scalars(
                select(SavedReport).order_by(SavedReport.created_at.desc()).limit(limit)
            )
            return [_out(r) for r in rows]
