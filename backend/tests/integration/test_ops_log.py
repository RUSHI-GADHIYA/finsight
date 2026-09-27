"""Audit log and run metrics against real Postgres. Rows are removed afterwards."""

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.models import AuditEvent, ResearchRun
from app.observability import ops as ops_module
from app.observability.ops import PostgresOpsLog

pytestmark = pytest.mark.integration


@pytest.fixture
async def ops(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[PostgresOpsLog]:
    engine = create_async_engine(get_settings().database_url)
    try:
        conn = await engine.connect()
        await conn.close()
    except (OSError, ConnectionError) as exc:
        await engine.dispose()
        pytest.skip(f"Postgres not reachable: {exc}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(ops_module, "get_sessionmaker", lambda: sessionmaker)
    yield PostgresOpsLog()
    async with sessionmaker() as session:
        await session.execute(delete(ResearchRun).where(ResearchRun.thread_id.like("test-%")))
        await session.execute(delete(AuditEvent).where(AuditEvent.thread_id.like("test-%")))
        await session.commit()
    await engine.dispose()


async def test_runs_and_audit_events_feed_the_metrics(ops: PostgresOpsLog) -> None:
    before = await ops.metrics()
    run_a, run_b = f"test-{uuid.uuid4().hex}", f"test-{uuid.uuid4().hex}"

    await ops.run_started(run_a, "NVIDIA risks?")
    await ops.run_updated(
        run_a,
        status="awaiting_approval",
        cost_usd=0.03,
        latency_ms=90_000,
        node_timings={"analyst": 30_000},
    )
    await ops.run_started(run_b, "NVIDIA risks?", cache_hit=True)
    await ops.run_updated(run_b, status="cached", cost_usd=0.0, latency_ms=0)
    await ops.event(run_a, "passage_dropped", {"chunk_id": 1})
    await ops.event(run_a, "report_approved", {"report_id": 1})

    after = await ops.metrics()
    assert after.runs == min(before.runs + 2, 200)
    assert after.guardrails["passage_dropped"] == before.guardrails["passage_dropped"] + 1
    assert after.reviews["report_approved"] == before.reviews["report_approved"] + 1
    newest = after.recent[-2:]
    assert [(p.status, p.cache_hit) for p in newest] == [
        ("awaiting_approval", False),
        ("cached", True),
    ]
    assert after.total_cost_usd >= 0.03
