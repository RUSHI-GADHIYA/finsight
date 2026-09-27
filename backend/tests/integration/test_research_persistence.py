"""Agent run persistence against real Postgres: the LangGraph checkpointer (pause in one
saver, resume in a fresh one, as after a server restart) and the reports table.

Skipped automatically when Postgres isn't reachable. Rows it writes are deleted afterwards.
"""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agents import store as store_module
from app.agents.graph import build_graph, checkpoint_serde
from app.agents.nodes import AgentDeps
from app.agents.state import ClaimVerdict, Critique, Report, ResearchPlan, ResearchState
from app.agents.store import PostgresReportStore
from app.config import get_settings
from app.db.models import SavedReport
from tests.agent_fakes import FakeLLM, FakeTools, cited, plan, report

pytestmark = pytest.mark.integration


@pytest.fixture
async def reports(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[PostgresReportStore]:
    engine = create_async_engine(get_settings().database_url)
    try:
        conn = await engine.connect()
        await conn.close()
    except (OSError, ConnectionError) as exc:
        await engine.dispose()
        pytest.skip(f"Postgres not reachable: {exc}")
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(store_module, "get_sessionmaker", lambda: sessionmaker)
    yield PostgresReportStore()
    async with sessionmaker() as session:
        await session.execute(delete(SavedReport).where(SavedReport.thread_id.like("test-%")))
        await session.commit()
    await engine.dispose()


async def test_pause_survives_a_new_checkpointer_and_saves_report(
    reports: PostgresReportStore,
) -> None:
    thread_id = f"test-{uuid.uuid4().hex}"
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    llm = FakeLLM(
        {
            ResearchPlan: [plan("NVDA")],
            Report: [report(cited("NVIDIA faces export controls.", 101))],
            Critique: [Critique(verdicts=[ClaimVerdict(claim_id=1, supported=True, reason="ok")])],
        }
    )
    deps = AgentDeps(tools=FakeTools(), llm=llm, store=reports, thread_id=thread_id)
    url = get_settings().checkpoint_database_url
    question: ResearchState = {"question": "NVIDIA export-control risks?"}

    async with AsyncPostgresSaver.from_conn_string(url, serde=checkpoint_serde()) as saver:
        await saver.setup()
        paused = await build_graph(saver).ainvoke(question, config, context=deps)
    assert "__interrupt__" in paused

    # A brand-new saver (think: server restart) picks the thread up from Postgres.
    async with AsyncPostgresSaver.from_conn_string(url, serde=checkpoint_serde()) as saver:
        graph = build_graph(saver)
        snapshot = await graph.aget_state(config)
        assert snapshot.next == ("human_review",)
        approve: Command[Any] = Command(resume={"action": "approve"})
        done = await graph.ainvoke(approve, config, context=deps)

    assert done["status"] == "approved"
    saved = await reports.get(done["report_id"])
    assert saved is not None
    assert saved.thread_id == thread_id
    assert saved.report.sections[0].claims[0].chunk_ids == [101]
    assert [r.id for r in await reports.recent()][0] == saved.id
