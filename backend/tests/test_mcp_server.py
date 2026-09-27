from datetime import date

import pytest
from mcp import Client

from app.mcp import server as mcp_server
from app.rag.financials import FinancialYear
from app.rag.market import MonthlyClose, PriceDataError, PriceSummary


async def test_lists_all_tools() -> None:
    async with Client(mcp_server.server) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert names == {
        "search_filings",
        "get_chunk",
        "list_filings",
        "get_financials",
        "get_price_history",
    }


async def test_get_financials_returns_structured_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(ticker: str, years: int) -> list[FinancialYear]:
        assert (ticker, years) == ("NVDA", 10)  # years is clamped to 10
        return [
            FinancialYear(
                fiscal_year_end=date(2025, 1, 26),
                revenue=100.0,
                gross_profit=75.0,
                operating_income=60.0,
                net_income=55.0,
                gross_margin=0.75,
                operating_margin=0.6,
                net_margin=0.55,
                source_accession="0001",
            )
        ]

    monkeypatch.setattr("app.rag.financials.get_financials", fake)
    async with Client(mcp_server.server) as client:
        result = await client.call_tool("get_financials", {"ticker": "NVDA", "years": 50})

    assert not result.is_error
    assert result.structured_content is not None
    assert result.structured_content["result"][0]["gross_margin"] == 0.75


async def test_missing_chunk_is_a_clean_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def not_found(session: object, chunk_id: int) -> None:
        return None

    monkeypatch.setattr("app.rag.retrieval.get_chunk", not_found)
    async with Client(mcp_server.server) as client:
        result = await client.call_tool("get_chunk", {"chunk_id": 42})

    assert result.is_error
    assert "No chunk with id 42" in result.content[0].text  # type: ignore[union-attr]


async def test_price_history_is_structured_and_errors_are_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_fetch(ticker: str, period: str) -> PriceSummary:
        if ticker == "NOPE":
            raise PriceDataError("No price history for 'NOPE'")
        closes = [MonthlyClose(month=date(2025, m, 1), close=100.0 + m) for m in (1, 2)]
        return PriceSummary(
            ticker=ticker,
            period=period,
            start_close=101.0,
            end_close=102.0,
            total_return=0.0099,
            monthly=closes,
        )

    monkeypatch.setattr("app.rag.market._fetch", fake_fetch)
    async with Client(mcp_server.server) as client:
        ok = await client.call_tool("get_price_history", {"ticker": "nvda", "period": "1y"})
        missing = await client.call_tool("get_price_history", {"ticker": "NOPE"})
        bad_period = await client.call_tool("get_price_history", {"ticker": "NVDA", "period": "9y"})

    assert ok.structured_content is not None
    assert ok.structured_content["ticker"] == "NVDA"
    assert len(ok.structured_content["monthly"]) == 2
    assert missing.is_error and "No price history" in missing.content[0].text  # type: ignore[union-attr]
    assert bad_period.is_error and "period must be one of" in bad_period.content[0].text  # type: ignore[union-attr]
