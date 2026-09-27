import logging
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agents.graph import ResearchGraph, build_graph, checkpoint_serde
from app.agents.nodes import AgentDeps, check_citations, figure_matches
from app.agents.state import ClaimVerdict, Critique, Figure, Report, ResearchPlan, ResearchState
from app.guardrails.injection import InjectionGuard
from tests.agent_fakes import (
    FYE,
    POISON,
    FakeClassifier,
    FakeLLM,
    FakeStore,
    FakeTools,
    cited,
    figure_claim,
    plan,
    report,
)

QUESTION = "Compare NVIDIA and AMD export-control risks and margins"


def all_supported(n: int) -> Critique:
    return Critique(
        verdicts=[ClaimVerdict(claim_id=i, supported=True, reason="ok") for i in range(1, n + 1)]
    )


def make(llm: FakeLLM, tools: FakeTools | None = None) -> tuple[ResearchGraph, AgentDeps]:
    graph = build_graph(InMemorySaver(serde=checkpoint_serde()))
    deps = AgentDeps(
        tools=tools or FakeTools(), llm=llm, store=FakeStore(), thread_id="t1", max_revisions=2
    )
    return graph, deps


async def run(
    graph: ResearchGraph, deps: AgentDeps, value: ResearchState | Command[Any]
) -> dict[str, Any]:
    out: dict[str, Any] = await graph.ainvoke(
        value, {"configurable": {"thread_id": deps.thread_id}}, context=deps
    )
    return out


async def test_happy_path_pauses_for_approval_then_saves(caplog: pytest.LogCaptureFixture) -> None:
    good = report(
        cited("NVIDIA faces export controls.", 101), cited("So does AMD.", 201), figure_claim(75.0)
    )
    llm = FakeLLM(
        {
            ResearchPlan: [plan("NVDA", "AMD", prices=True)],
            Report: [good],
            Critique: [all_supported(2)],
        }
    )
    tools = FakeTools(fail_prices_for={"AMD"})
    graph, deps = make(llm, tools)

    with caplog.at_level(logging.WARNING):
        state = await run(graph, deps, {"question": QUESTION})

    assert "__interrupt__" in state
    pending = state["__interrupt__"][0].value
    assert pending["report"]["title"] == "NVDA vs AMD"
    assert state["status"] == "awaiting_approval"
    assert state["warnings"] == []
    # filings and market both ran (parallel branches joined before the analyst)
    assert {c.citation.ticker for c in state["evidence"]} == {"NVDA", "AMD"}
    assert set(state["financials"]) == {"NVDA", "AMD"}
    assert set(state["prices"]) == {"NVDA"}
    # the price failure degraded the run instead of failing it
    assert any("price data unavailable for AMD" in e for e in state["errors"])
    # the judge sees the summary and the figure claim's wording, with the table rows
    judged = llm.calls["critic"][0]
    assert "claim 0 (summary)" in judged
    assert "claim 3 (passages []; figures [NVDA gross_margin_pct 2025-01-26 = 75.0])" in judged
    assert "TABLES" in judged
    assert state["cost_usd"] == pytest.approx(0.003)

    final = await run(graph, deps, Command(resume={"action": "approve"}))
    assert final["status"] == "approved"
    assert final["report_id"] == 1
    assert deps.store.saved[0]["report"] == good  # type: ignore[attr-defined]
    # checkpoints only rebuild allowlisted types
    assert "blocked deserialization" not in caplog.text.lower()
    assert "unregistered type" not in caplog.text.lower()


async def test_judge_catches_wording_that_misdescribes_correct_figures() -> None:
    # Regression from a live run: exact margins, but "expanded" when they had fallen.
    misworded = report(figure_claim(75.0))
    fixed = report(figure_claim(75.0))
    wrong_direction = Critique(
        verdicts=[ClaimVerdict(claim_id=1, supported=False, reason="margin fell, not rose")]
    )
    llm = FakeLLM(
        {
            ResearchPlan: [plan("NVDA")],
            Report: [misworded, fixed],
            Critique: [wrong_direction, all_supported(1)],
        }
    )
    graph, deps = make(llm)

    state = await run(graph, deps, {"question": QUESTION})

    assert len(llm.calls["analyst"]) == 2
    assert "claim 1: margin fell, not rose" in llm.calls["analyst"][1]
    assert state["revisions"] == 1
    assert state["warnings"] == []


async def test_invented_citation_triggers_a_revision() -> None:
    bad = report(cited("Made-up claim.", 999))
    fixed = report(cited("NVIDIA faces export controls.", 101))
    llm = FakeLLM(
        {ResearchPlan: [plan("NVDA")], Report: [bad, fixed], Critique: [all_supported(1)]}
    )
    graph, deps = make(llm)

    state = await run(graph, deps, {"question": QUESTION})

    assert len(llm.calls["analyst"]) == 2
    assert "not retrieved: [999]" in llm.calls["analyst"][1]  # feedback reached the analyst
    assert len(llm.calls["critic"]) == 1  # the invented citation never cost a judge call
    assert state["revisions"] == 1
    assert state["report"] == fixed
    assert state["warnings"] == []


async def test_revision_loop_stops_at_max_and_warns() -> None:
    bad = report(cited("Made-up claim.", 999))
    llm = FakeLLM({ResearchPlan: [plan("NVDA")], Report: [bad], Critique: [all_supported(1)]})
    graph, deps = make(llm)

    state = await run(graph, deps, {"question": QUESTION})

    assert len(llm.calls["analyst"]) == 3  # first draft + max_revisions
    assert state["revisions"] == 2
    assert "__interrupt__" in state
    assert state["warnings"] and "Claim 1 may be unsupported" in state["warnings"][0]


async def test_edit_resume_saves_the_human_version() -> None:
    llm = FakeLLM(
        {
            ResearchPlan: [plan("NVDA")],
            Report: [report(cited("x", 101))],
            Critique: [all_supported(1)],
        }
    )
    graph, deps = make(llm)
    await run(graph, deps, {"question": QUESTION})

    edited = report(cited("Edited by a human.", 101)).model_dump(mode="json")
    final = await run(graph, deps, Command(resume={"action": "edit", "report": edited}))

    assert final["status"] == "approved"
    assert final["report"].sections[0].claims[0].text == "Edited by a human."


async def test_reject_does_not_save() -> None:
    llm = FakeLLM(
        {
            ResearchPlan: [plan("NVDA")],
            Report: [report(cited("x", 101))],
            Critique: [all_supported(1)],
        }
    )
    graph, deps = make(llm)
    await run(graph, deps, {"question": QUESTION})

    final = await run(graph, deps, Command(resume={"action": "reject"}))

    assert final["status"] == "rejected"
    assert deps.store.saved == []  # type: ignore[attr-defined]


async def test_unknown_company_ends_early_without_retrieval() -> None:
    llm = FakeLLM({ResearchPlan: [plan("TSLA")]})
    tools = FakeTools()
    graph, deps = make(llm, tools)

    state = await run(graph, deps, {"question": "How is Tesla doing?"})

    assert state["status"] == "no_data"
    assert "Available: AMD, NVDA" in state["message"]
    assert tools.searches == []
    assert "analyst" not in llm.calls


def test_figure_matching_tolerates_rounding_only() -> None:
    table = {
        ("NVDA", FYE.isoformat(), "gross_margin_pct"): 75.0,
        ("NVDA", FYE.isoformat(), "revenue_musd"): 130497.0,
    }

    def fig(metric: str, value: float) -> Figure:
        return Figure(
            ticker="nvda",
            fiscal_year_end=FYE.isoformat(),
            metric=metric,
            value=value,
        )

    assert figure_matches(fig("gross_margin_pct", 75.1), table)
    assert not figure_matches(fig("gross_margin_pct", 76.0), table)
    assert figure_matches(fig("revenue_musd", 130500), table)  # "$130.5B"
    assert not figure_matches(fig("revenue_musd", 135000), table)
    assert not figure_matches(fig("net_margin_pct", 50.0), table)  # not in the table


def test_check_citations_flags_unsourced_and_wrong_figures() -> None:
    claims = report(cited("ok", 101), cited("no source"), figure_claim(80.0)).claims()
    table = {("NVDA", FYE.isoformat(), "gross_margin_pct"): 75.0}

    failures = check_citations(claims, {101}, table)

    assert set(failures) == {2, 3}
    assert failures[2].reason == "no source cited"
    assert "gross_margin_pct" in failures[3].reason


async def test_planted_injection_in_a_filing_is_dropped_before_the_analyst() -> None:
    llm = FakeLLM(
        {
            ResearchPlan: [plan("NVDA")],
            Report: [report(cited("NVIDIA faces export controls.", 101))],
            Critique: [all_supported(1)],
        }
    )
    graph, deps = make(llm, FakeTools(poisoned={102}))
    deps.guard = InjectionGuard(FakeClassifier(), threshold=0.9)

    state = await run(graph, deps, {"question": QUESTION})

    assert [c.citation.chunk_id for c in state["evidence"]] == [101]
    assert POISON not in llm.calls["analyst"][0]  # the model never saw the planted text
    assert any("Dropped passage #102" in e for e in state["errors"])
