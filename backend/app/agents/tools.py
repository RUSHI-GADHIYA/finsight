"""The agents' only way to reach data: the FinSight MCP server, through an MCP client.

In-process by default (`mcp.Client(server)`, no extra process), or streamable HTTP when
`settings.mcp_url` is set, so the same agents work against a separately deployed server.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol

from mcp import Client
from pydantic import BaseModel, TypeAdapter

from app.config import get_settings
from app.rag.financials import FinancialYear
from app.rag.market import PriceSummary
from app.rag.retrieval import FilingSummary, RetrievedChunk

log = logging.getLogger(__name__)


class ToolCallError(RuntimeError):
    """A tool failed; nodes record it in state and carry on with what they have."""


class ResearchTools(Protocol):
    async def list_filings(self) -> list[FilingSummary]: ...

    async def search(
        self, query: str, tickers: list[str], sections: list[str], top_k: int
    ) -> list[RetrievedChunk]: ...

    async def financials(self, ticker: str, years: int) -> list[FinancialYear]: ...

    async def prices(self, ticker: str, period: str) -> PriceSummary: ...


class McpResearchTools:
    """Typed wrapper over one open MCP client session."""

    def __init__(self, client: Client) -> None:
        self._client = client

    async def _call(self, tool: str, args: dict[str, Any]) -> Any:
        last_exc: Exception | None = None
        for attempt in (1, 2):  # one retry for transport hiccups; tool errors are final
            try:
                result = await self._client.call_tool(tool, args)
            except Exception as exc:  # noqa: BLE001 - transport errors vary by transport
                log.warning("MCP %s failed (attempt %d): %s", tool, attempt, exc)
                last_exc = exc
                continue
            if result.is_error:
                text = " ".join(getattr(c, "text", "") for c in result.content)
                raise ToolCallError(f"{tool}: {text or 'tool error'}")
            data = result.structured_content
            if data is None:
                raise ToolCallError(f"{tool}: no structured result")
            # Non-object return types (lists) are wrapped as {"result": ...} by the server.
            return data["result"] if set(data) == {"result"} else data
        raise ToolCallError(f"{tool}: unreachable ({last_exc})")

    async def list_filings(self) -> list[FilingSummary]:
        return _parse(list[FilingSummary], await self._call("list_filings", {}))

    async def search(
        self, query: str, tickers: list[str], sections: list[str], top_k: int
    ) -> list[RetrievedChunk]:
        args = {"query": query, "tickers": tickers, "sections": sections or None, "top_k": top_k}
        return _parse(list[RetrievedChunk], await self._call("search_filings", args))

    async def financials(self, ticker: str, years: int) -> list[FinancialYear]:
        data = await self._call("get_financials", {"ticker": ticker, "years": years})
        return _parse(list[FinancialYear], data)

    async def prices(self, ticker: str, period: str) -> PriceSummary:
        data = await self._call("get_price_history", {"ticker": ticker, "period": period})
        return _parse(PriceSummary, data)


def _parse[T](kind: type[T] | Any, data: Any) -> T:
    if isinstance(kind, type) and issubclass(kind, BaseModel):
        return kind.model_validate(data)  # type: ignore[return-value]
    return TypeAdapter(kind).validate_python(data)  # type: ignore[no-any-return]


@asynccontextmanager
async def mcp_tools() -> AsyncIterator[McpResearchTools]:
    """Open an MCP session for one research run."""
    url = get_settings().mcp_url
    if url:
        target: Any = url
    else:
        from app.mcp.server import server  # in-process; imported lazily to avoid a cycle

        target = server
    async with Client(target) as client:
        yield McpResearchTools(client)
