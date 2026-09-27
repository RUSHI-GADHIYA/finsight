"""The agents' MCP client against the real in-process FinSight MCP server."""

import pytest

from app.agents.tools import ToolCallError, mcp_tools
from app.rag.financials import FinancialYear
from app.rag.market import PriceDataError, PriceSummary
from tests.agent_fakes import FYE, FakeTools


async def test_typed_results_round_trip_through_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeTools()

    async def financials(ticker: str, years: int) -> list[FinancialYear]:
        return await fake.financials(ticker, years)

    def fetch(ticker: str, period: str) -> PriceSummary:
        if ticker == "AMD":
            raise PriceDataError("No price history for 'AMD'")
        return PriceSummary(
            ticker=ticker,
            period=period,
            start_close=1.0,
            end_close=2.0,
            total_return=1.0,
            monthly=[],
        )

    monkeypatch.setattr("app.rag.financials.get_financials", financials)
    monkeypatch.setattr("app.rag.market._fetch", fetch)

    async with mcp_tools() as tools:
        years = await tools.financials("NVDA", 3)  # list result, unwrapped from {"result": [...]}
        prices = await tools.prices("NVDA", "2y")  # object result
        with pytest.raises(ToolCallError, match="No price history for 'AMD'"):
            await tools.prices("AMD", "2y")

    assert isinstance(years[0], FinancialYear) and years[0].fiscal_year_end == FYE
    assert isinstance(prices, PriceSummary) and prices.total_return == 1.0
