"""FinSight MCP server: SEC filing search and XBRL financials as MCP tools.

uv run python -m app.mcp.server                 # stdio (Claude Desktop, Cursor, Inspector)
uv run python -m app.mcp.server --http --port 8001   # streamable HTTP at /mcp (agents)
"""

import argparse
import logging

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from app.db.session import get_sessionmaker
from app.rag import financials, market, retrieval
from app.rag.financials import FinancialYear
from app.rag.market import PriceSummary
from app.rag.retrieval import FilingSummary, RetrievedChunk, SearchFilters

server = MCPServer(
    name="finsight",
    title="FinSight SEC Research",
    instructions=(
        "Research US public companies from their SEC 10-K filings. Use search_filings for "
        "qualitative questions (risks, strategy, MD&A commentary) and cite results by "
        "chunk_id; use get_financials for revenue, profit and margins, and "
        "get_price_history for share-price performance. Call list_filings first to see "
        "which companies and years are available."
    ),
    version="0.3.0",
)


@server.tool()
async def search_filings(
    query: str,
    tickers: list[str] | None = None,
    sections: list[str] | None = None,
    top_k: int = 8,
) -> list[RetrievedChunk]:
    """Hybrid semantic + keyword search over ingested SEC filings, reranked for relevance.

    Args:
        query: Natural-language question, e.g. "impact of export controls on China sales".
        tickers: Optional ticker filter, e.g. ["NVDA", "AMD"].
        sections: Optional filter: business, risk_factors, mdna, market_risk, legal_proceedings.
        top_k: Number of passages to return (1-20).
    """
    async with get_sessionmaker()() as session:
        return await retrieval.search(
            session,
            query,
            SearchFilters(tickers=tickers, sections=sections),
            top_k=max(1, min(top_k, 20)),
        )


@server.tool()
async def get_chunk(chunk_id: int) -> RetrievedChunk:
    """Fetch one filing passage and its citation by chunk_id (as returned by search_filings)."""
    async with get_sessionmaker()() as session:
        chunk = await retrieval.get_chunk(session, chunk_id)
    if chunk is None:
        # ToolError reaches the client with its message; other exceptions are logged as crashes.
        raise ToolError(f"No chunk with id {chunk_id}")
    return chunk


@server.tool()
async def list_filings(ticker: str | None = None) -> list[FilingSummary]:
    """List ingested filings (optionally for one ticker) with their available sections."""
    async with get_sessionmaker()() as session:
        return await retrieval.list_filings(session, ticker)


@server.tool()
async def get_financials(ticker: str, years: int = 3) -> list[FinancialYear]:
    """Annual revenue, gross/operating/net income and margins from SEC XBRL data.

    Args:
        ticker: Company ticker, e.g. "NVDA".
        years: Most recent fiscal years to return (1-10).
    """
    return await financials.get_financials(ticker, max(1, min(years, 10)))


@server.tool()
async def get_price_history(ticker: str, period: str = "2y") -> PriceSummary:
    """Monthly share-price closes and total return (Yahoo Finance, adjusted for splits).

    Args:
        ticker: Company ticker, e.g. "NVDA".
        period: One of 6mo, 1y, 2y, 5y.
    """
    try:
        return await market.get_price_history(ticker, period)
    except (market.PriceDataError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="FinSight MCP server")
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP, not stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()

    # stdout is the MCP channel in stdio mode; keep library chatter quiet.
    for noisy in ("httpx", "sentence_transformers", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if args.http:
        server.run("streamable-http", host=args.host, port=args.port)
    else:
        server.run()


if __name__ == "__main__":
    main()
