"""Annual income-statement figures and margins from SEC XBRL company facts.

Structured numbers straight from the filings, so margins are exact and citable (accession
number), unlike numbers an LLM might read off a table.
"""

from datetime import date
from typing import Any

from pydantic import BaseModel

from app.config import get_settings
from app.rag.edgar_client import EdgarClient

# Companies switch XBRL tags over the years; first tag with a value for a period wins.
METRIC_TAGS: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenuesNetOfInterestExpense",
    ],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold"],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
}
_ANNUAL_DAYS = range(350, 380)  # a fiscal year is 52/53 weeks


class FinancialYear(BaseModel):
    fiscal_year_end: date
    revenue: float | None
    gross_profit: float | None
    operating_income: float | None
    net_income: float | None
    gross_margin: float | None
    operating_margin: float | None
    net_margin: float | None
    source_accession: str | None  # latest 10-K reporting this year's revenue


def _annual_values(facts: dict[str, Any], tag: str) -> dict[date, tuple[float, str, str]]:
    """period end -> (value, accession, filed) for full-year 10-K facts of one tag.

    A 10-K also restates prior years, so the same period appears several times; keep the
    most recently filed value.
    """
    units = facts.get("facts", {}).get("us-gaap", {}).get(tag, {}).get("units", {})
    out: dict[date, tuple[float, str, str]] = {}
    for fact in units.get("USD", []):
        if not fact.get("form", "").startswith("10-K") or "start" not in fact:
            continue
        start, end = date.fromisoformat(fact["start"]), date.fromisoformat(fact["end"])
        if (end - start).days not in _ANNUAL_DAYS:
            continue
        prev = out.get(end)
        if prev is None or fact["filed"] > prev[2]:
            out[end] = (float(fact["val"]), fact["accn"], fact["filed"])
    return out


def _metric(facts: dict[str, Any], metric: str) -> dict[date, tuple[float, str, str]]:
    merged: dict[date, tuple[float, str, str]] = {}
    for tag in METRIC_TAGS[metric]:
        for end, value in _annual_values(facts, tag).items():
            merged.setdefault(end, value)
    return merged


def _ratio(num: float | None, den: float | None) -> float | None:
    return round(num / den, 4) if num is not None and den else None


def annual_financials(facts: dict[str, Any], years: int = 3) -> list[FinancialYear]:
    metrics = {name: _metric(facts, name) for name in METRIC_TAGS}
    revenue = metrics["revenue"]
    rows = []
    for end in sorted(revenue, reverse=True)[:years]:
        rev = revenue[end][0]

        def val(name: str, end: date = end) -> float | None:
            hit = metrics[name].get(end)
            return hit[0] if hit else None

        gross = val("gross_profit")
        cost = val("cost_of_revenue")
        if gross is None and cost is not None:
            gross = rev - cost
        op, net = val("operating_income"), val("net_income")
        rows.append(
            FinancialYear(
                fiscal_year_end=end,
                revenue=rev,
                gross_profit=gross,
                operating_income=op,
                net_income=net,
                gross_margin=_ratio(gross, rev),
                operating_margin=_ratio(op, rev),
                net_margin=_ratio(net, rev),
                source_accession=revenue[end][1],
            )
        )
    return rows


async def get_financials(ticker: str, years: int = 3) -> list[FinancialYear]:
    async with EdgarClient(get_settings().sec_user_agent) as edgar:
        return annual_financials(await edgar.company_facts(ticker), years)
