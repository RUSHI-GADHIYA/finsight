"""Graph state and the structured outputs the agents exchange.

Everything in the state is a Pydantic model or plain data, so checkpoints serialize
cleanly and the report the human approves is schema-validated end to end.
"""

import operator
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field

from app.rag.financials import FinancialYear
from app.rag.market import MonthlyClose, PriceSummary
from app.rag.retrieval import Citation, RetrievedChunk

Section = Literal["business", "risk_factors", "mdna", "market_risk", "legal_proceedings"]
Metric = Literal[
    "revenue_musd",
    "gross_profit_musd",
    "operating_income_musd",
    "net_income_musd",
    "gross_margin_pct",
    "operating_margin_pct",
    "net_margin_pct",
    "price_return_pct",
]
Status = Literal["running", "no_data", "awaiting_approval", "approved", "rejected"]


class ResearchPlan(BaseModel):
    """Supervisor output: what to look up, before any retrieval happens."""

    tickers: list[str] = Field(description="Tickers from the available list, at most 4")
    filing_queries: list[str] = Field(
        description="1-3 focused search queries for the filings, without company names"
    )
    sections: list[Section] = Field(description="Sections to search; empty means all")
    need_financials: bool = Field(description="Revenue, profit or margin figures needed")
    need_prices: bool = Field(description="Share-price performance needed")


class Figure(BaseModel):
    """A number used in a claim, tied to the XBRL/price table it came from."""

    ticker: str
    fiscal_year_end: str = Field(description="YYYY-MM-DD as in the table; '' for prices")
    metric: Metric
    value: float = Field(description="Same units as the table: $ millions or percent")


class Claim(BaseModel):
    text: str
    chunk_ids: list[int] = Field(description="Filing passages that support the claim")
    figures: list[Figure] = Field(description="Table figures the claim relies on")


class ReportSection(BaseModel):
    heading: str
    claims: list[Claim]


class Report(BaseModel):
    title: str
    summary: str
    sections: list[ReportSection]

    def claims(self) -> list[Claim]:
        return [claim for section in self.sections for claim in section.claims]


class ClaimVerdict(BaseModel):
    claim_id: int = Field(description="The number shown next to the claim")
    supported: bool
    reason: str = Field(description="Short reason; say what is missing if unsupported")


class Critique(BaseModel):
    verdicts: list[ClaimVerdict]

    def unsupported(self) -> list[ClaimVerdict]:
        return [v for v in self.verdicts if not v.supported]


def merge_evidence(left: list[RetrievedChunk], right: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Reducer: append new passages, keeping the first copy of each chunk_id."""
    seen = {c.citation.chunk_id for c in left}
    return left + [c for c in right if c.citation.chunk_id not in seen]


def merge_dicts[V](left: dict[str, V], right: dict[str, V]) -> dict[str, V]:
    return {**left, **right}


class ResearchState(TypedDict, total=False):
    question: str
    plan: ResearchPlan
    evidence: Annotated[list[RetrievedChunk], merge_evidence]
    financials: Annotated[dict[str, list[FinancialYear]], merge_dicts]
    prices: Annotated[dict[str, PriceSummary], merge_dicts]
    report: Report
    critique: Critique
    revisions: int
    warnings: list[str]  # claims still unsupported when the revision budget ran out
    errors: Annotated[list[str], operator.add]  # tool failures; the run degrades, not fails
    cost_usd: Annotated[float, operator.add]
    status: Status
    message: str  # user-facing note, e.g. why nothing could be researched
    report_id: int


# Types that may appear in checkpoints; the checkpointer's msgpack allowlist is built from
# this so resuming a thread can never construct arbitrary classes.
CHECKPOINT_TYPES: tuple[type, ...] = (
    ResearchPlan,
    Figure,
    Claim,
    ReportSection,
    Report,
    ClaimVerdict,
    Critique,
    RetrievedChunk,
    Citation,
    FinancialYear,
    PriceSummary,
    MonthlyClose,
)
