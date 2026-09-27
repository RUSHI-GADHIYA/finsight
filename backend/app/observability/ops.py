"""Operational records: the audit log and per-run cost/latency, plus the /metrics summary.

Writes are best-effort: a logging failure must never break or block a research run.
"""

import logging
import math
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel
from sqlalchemy import func, select

from app.db.models import AuditEvent, ResearchRun
from app.db.session import get_sessionmaker

log = logging.getLogger(__name__)

GUARDRAIL_EVENTS = ("input_blocked", "passage_dropped", "pii_redacted")
REVIEW_EVENTS = ("report_approved", "report_edited", "report_rejected")


class OpsLog(Protocol):
    async def event(self, thread_id: str | None, event: str, detail: dict[str, Any]) -> None: ...

    async def run_started(
        self, thread_id: str, question: str, *, cache_hit: bool = False
    ) -> None: ...

    async def run_updated(
        self,
        thread_id: str,
        *,
        status: str,
        cost_usd: float | None = None,
        latency_ms: int | None = None,
        node_timings: dict[str, int] | None = None,
    ) -> None: ...


class RunPoint(BaseModel):
    created_at: datetime
    status: str
    cost_usd: float
    latency_ms: int | None
    cache_hit: bool


class Metrics(BaseModel):
    runs: int
    total_cost_usd: float
    mean_cost_usd: float  # runs that called the LLMs (cache hits excluded)
    latency_p50_ms: int | None
    latency_p95_ms: int | None
    cache_hit_rate: float
    guardrails: dict[str, int]
    reviews: dict[str, int]
    recent: list[RunPoint]  # newest last, for charts


def percentile(values: list[int], q: float) -> int | None:
    """Nearest-rank percentile; None for no data."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[rank - 1]


class PostgresOpsLog:
    async def event(self, thread_id: str | None, event: str, detail: dict[str, Any]) -> None:
        try:
            async with get_sessionmaker()() as session:
                session.add(AuditEvent(thread_id=thread_id, event=event, detail=detail))
                await session.commit()
        except Exception:
            log.warning("audit log write failed (%s)", event, exc_info=True)

    async def run_started(self, thread_id: str, question: str, *, cache_hit: bool = False) -> None:
        try:
            async with get_sessionmaker()() as session:
                session.add(
                    ResearchRun(
                        thread_id=thread_id,
                        question=question,
                        status="running",
                        cost_usd=0.0,
                        node_timings={},
                        cache_hit=cache_hit,
                    )
                )
                await session.commit()
        except Exception:
            log.warning("run log write failed", exc_info=True)

    async def run_updated(
        self,
        thread_id: str,
        *,
        status: str,
        cost_usd: float | None = None,
        latency_ms: int | None = None,
        node_timings: dict[str, int] | None = None,
    ) -> None:
        try:
            async with get_sessionmaker()() as session:
                run = await session.get(ResearchRun, thread_id)
                if run is None:
                    return
                run.status = status
                if cost_usd is not None:
                    run.cost_usd = cost_usd
                if latency_ms is not None:
                    run.latency_ms = latency_ms
                if node_timings is not None:
                    run.node_timings = node_timings
                run.finished_at = datetime.now(UTC)
                await session.commit()
        except Exception:
            log.warning("run log update failed", exc_info=True)

    async def metrics(self, limit: int = 200) -> Metrics:
        async with get_sessionmaker()() as session:
            rows = list(
                await session.scalars(
                    select(ResearchRun).order_by(ResearchRun.created_at.desc()).limit(limit)
                )
            )
            grouped = await session.execute(
                select(AuditEvent.event, func.count()).group_by(AuditEvent.event)
            )
            counts: dict[str, int] = {event: n for event, n in grouped}
        llm_runs = [r for r in rows if not r.cache_hit]
        latencies = [r.latency_ms for r in llm_runs if r.latency_ms is not None]
        return Metrics(
            runs=len(rows),
            total_cost_usd=round(sum(r.cost_usd for r in rows), 6),
            mean_cost_usd=round(sum(r.cost_usd for r in llm_runs) / len(llm_runs), 6)
            if llm_runs
            else 0.0,
            latency_p50_ms=percentile(latencies, 0.5),
            latency_p95_ms=percentile(latencies, 0.95),
            cache_hit_rate=round(sum(r.cache_hit for r in rows) / len(rows), 4) if rows else 0.0,
            guardrails={e: counts.get(e, 0) for e in GUARDRAIL_EVENTS},
            reviews={e: counts.get(e, 0) for e in REVIEW_EVENTS},
            recent=[
                RunPoint(
                    created_at=r.created_at,
                    status=r.status,
                    cost_usd=r.cost_usd,
                    latency_ms=r.latency_ms,
                    cache_hit=r.cache_hit,
                )
                for r in reversed(rows[:50])
            ],
        )
