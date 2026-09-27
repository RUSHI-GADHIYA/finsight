import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.graph import build_graph, checkpoint_serde
from app.agents.runner import ResearchService
from app.agents.state import ClaimVerdict, Critique, Report, ResearchPlan
from app.agents.tools import ResearchTools
from app.main import app
from app.observability.cost import BudgetExceeded
from tests.agent_fakes import FakeLLM, FakeStore, FakeTools, cited, plan, report


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in body.replace("\r\n", "\n").strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        if "event" in fields:
            events.append((fields["event"], json.loads(fields["data"])))
    return events


@pytest.fixture
def service() -> ResearchService:
    llm = FakeLLM(
        {
            ResearchPlan: [plan("NVDA")],
            Report: [report(cited("NVIDIA faces export controls.", 101))],
            Critique: [Critique(verdicts=[ClaimVerdict(claim_id=1, supported=True, reason="ok")])],
        }
    )

    @asynccontextmanager
    async def tools() -> AsyncIterator[ResearchTools]:
        yield FakeTools()

    svc = ResearchService(
        graph=build_graph(InMemorySaver(serde=checkpoint_serde())),
        store=FakeStore(),
        tools_factory=tools,
        llm_factory=lambda tracker: llm,
    )
    app.state.research = svc  # no lifespan under ASGITransport; inject directly
    return svc


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_research_streams_to_approval_then_resumes(
    service: ResearchService, client: httpx.AsyncClient
) -> None:
    resp = await client.post("/research", json={"question": "NVIDIA export control risks?"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(resp.text)
    kinds = [e for e, _ in events]

    assert kinds[0] == "run_started"
    assert kinds[-1] == "awaiting_approval"
    finished = [d["node"] for e, d in events if e == "node_finished"]
    assert finished[0] == "supervisor"
    assert set(finished[1:3]) == {"filings", "market"}  # parallel branches, either order
    assert finished[3:] == ["analyst", "critic"]
    assert "token" in kinds and "cost" in kinds
    thread_id = events[0][1]["thread_id"]
    pending = events[-1][1]
    assert pending["report"]["title"] == "NVDA vs AMD"
    # the reviewer gets the cited passage text and the tables behind the figures
    assert [s["citation"]["chunk_id"] for s in pending["context"]["sources"]] == [101]
    assert "NVDA passage 101" in pending["context"]["sources"][0]["text"]
    assert set(pending["context"]["financials"]) == {"NVDA"}

    # a page refresh can recover the pending review from the checkpoint
    status = (await client.get(f"/research/{thread_id}")).json()
    assert status["status"] == "awaiting_approval"
    assert status["question"] == "NVIDIA export control risks?"
    assert status["pending"]["report"] == pending["report"]

    resumed = parse_sse(
        (await client.post(f"/research/{thread_id}/resume", json={"action": "approve"})).text
    )
    assert resumed[-1] == (
        "done",
        {
            "thread_id": thread_id,
            "status": "approved",
            "report_id": 1,
            "message": None,
            "cost_usd": 0.003,
        },
    )


async def test_status_after_approval_and_for_unknown_threads(
    service: ResearchService, client: httpx.AsyncClient
) -> None:
    events = parse_sse((await client.post("/research", json={"question": "NVDA risks?"})).text)
    thread_id = events[0][1]["thread_id"]
    await client.post(f"/research/{thread_id}/resume", json={"action": "approve"})

    status = (await client.get(f"/research/{thread_id}")).json()
    assert status["status"] == "approved"
    assert status["pending"] is None
    assert status["report_id"] == 1
    assert (await client.get("/research/nope")).status_code == 404


async def test_cors_allows_the_ui_origin(client: httpx.AsyncClient) -> None:
    resp = await client.options(
        "/research",
        headers={
            "origin": "http://localhost:3000",
            "access-control-request-method": "POST",
            "access-control-request-headers": "content-type",
        },
    )
    assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"


async def test_resume_rejects_unknown_thread_and_bad_edit(
    service: ResearchService, client: httpx.AsyncClient
) -> None:
    assert (
        await client.post("/research/nope/resume", json={"action": "approve"})
    ).status_code == 409
    bad_edit = await client.post("/research/nope/resume", json={"action": "edit"})
    assert bad_edit.status_code == 422


async def test_budget_stop_is_an_error_event(
    service: ResearchService, client: httpx.AsyncClient
) -> None:
    class BrokeLLM(FakeLLM):
        async def parse(self, **kwargs: Any) -> Any:
            raise BudgetExceeded("Spent $0.2000, over the $0.10 budget")

    service.llm_factory = lambda tracker: BrokeLLM({})
    events = parse_sse((await client.post("/research", json={"question": "NVDA risks?"})).text)

    assert events[-1][0] == "error"
    assert "Budget stop" in events[-1][1]["message"]
