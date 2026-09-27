"""Deterministic stand-ins for the MCP tools, the LLM and the report store."""

from collections import defaultdict
from datetime import date
from typing import Any

from pydantic import BaseModel

from app.agents.llm import StreamWriter, Tier
from app.agents.state import Claim, Figure, Report, ReportSection, ResearchPlan
from app.agents.tools import ToolCallError
from app.rag.financials import FinancialYear
from app.rag.market import MonthlyClose, PriceSummary
from app.rag.retrieval import Citation, FilingSummary, RetrievedChunk

FYE = date(2025, 1, 26)
CHUNK_IDS = {"NVDA": [101, 102], "AMD": [201, 202]}


def chunk(chunk_id: int, ticker: str) -> RetrievedChunk:
    return RetrievedChunk(
        citation=Citation(
            chunk_id=chunk_id,
            ticker=ticker,
            form="10-K",
            report_date=FYE,
            section="risk_factors",
            url=f"https://sec.example/{ticker}",
        ),
        text=f"{ticker} passage {chunk_id}: export controls may reduce data center sales.",
        score=1.0,
    )


class FakeTools:
    def __init__(self, *, fail_prices_for: set[str] | None = None) -> None:
        self.fail_prices_for = fail_prices_for or set()
        self.searches: list[tuple[str, list[str]]] = []

    async def list_filings(self) -> list[FilingSummary]:
        return [
            FilingSummary(
                ticker=t,
                form="10-K",
                report_date=FYE,
                filing_date=FYE,
                accession_no=f"acc-{t}",
                url=f"https://sec.example/{t}",
                sections=["risk_factors"],
                chunks=2,
            )
            for t in CHUNK_IDS
        ]

    async def search(
        self, query: str, tickers: list[str], sections: list[str], top_k: int
    ) -> list[RetrievedChunk]:
        self.searches.append((query, tickers))
        return [chunk(cid, t) for t in tickers for cid in CHUNK_IDS[t]][:top_k]

    async def financials(self, ticker: str, years: int) -> list[FinancialYear]:
        return [
            FinancialYear(
                fiscal_year_end=FYE,
                revenue=130_497_000_000,
                gross_profit=97_858_000_000,
                operating_income=81_453_000_000,
                net_income=72_880_000_000,
                gross_margin=0.7499,
                operating_margin=0.6242,
                net_margin=0.5585,
                source_accession=f"acc-{ticker}",
            )
        ]

    async def prices(self, ticker: str, period: str) -> PriceSummary:
        if ticker in self.fail_prices_for:
            raise ToolCallError(f"get_price_history: No price history for {ticker!r}")
        return PriceSummary(
            ticker=ticker,
            period=period,
            start_close=100.0,
            end_close=150.0,
            total_return=0.5,
            monthly=[MonthlyClose(month=FYE, close=100.0), MonthlyClose(month=FYE, close=150.0)],
        )


class FakeLLM:
    """Returns scripted outputs per schema, in order; the last one repeats."""

    def __init__(self, outputs: dict[type[BaseModel], list[BaseModel]]) -> None:
        self.outputs = outputs
        self.calls: dict[str, list[str]] = defaultdict(list)  # node -> inputs

    async def parse[T: BaseModel](
        self,
        *,
        node: str,
        tier: Tier,
        instructions: str,
        input: str,
        schema: type[T],
        max_output_tokens: int,
        writer: StreamWriter,
    ) -> tuple[T, float]:
        self.calls[node].append(input)
        queue = self.outputs[schema]
        out = queue.pop(0) if len(queue) > 1 else queue[0]
        writer({"type": "token", "node": node, "delta": "{"})
        writer({"type": "cost", "node": node, "call_usd": 0.001})
        assert isinstance(out, schema)
        return out, 0.001


class FakeStore:
    def __init__(self) -> None:
        self.saved: list[dict[str, Any]] = []

    async def save(
        self,
        *,
        thread_id: str,
        question: str,
        report: Report,
        warnings: list[str],
        cost_usd: float,
    ) -> int:
        self.saved.append({"thread_id": thread_id, "report": report, "warnings": warnings})
        return len(self.saved)


def plan(*tickers: str, prices: bool = False) -> ResearchPlan:
    return ResearchPlan(
        tickers=list(tickers),
        filing_queries=["export controls data center"],
        sections=["risk_factors"],
        need_financials=True,
        need_prices=prices,
    )


def report(*claims: Claim) -> Report:
    return Report(
        title="NVDA vs AMD",
        summary="Both face export-control risk.",
        sections=[ReportSection(heading="Risks", claims=list(claims))],
    )


def cited(text: str, *chunk_ids: int) -> Claim:
    return Claim(text=text, chunk_ids=list(chunk_ids), figures=[])


def figure_claim(value: float) -> Claim:
    return Claim(
        text=f"NVIDIA's gross margin was {value}%.",
        chunk_ids=[],
        figures=[
            Figure(
                ticker="NVDA",
                fiscal_year_end=FYE.isoformat(),
                metric="gross_margin_pct",
                value=value,
            )
        ],
    )
