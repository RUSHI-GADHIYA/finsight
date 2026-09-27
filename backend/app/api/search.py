from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.rag.retrieval import (
    FilingSummary,
    RetrievedChunk,
    SearchFilters,
    SearchMode,
    list_filings,
    search,
)

router = APIRouter(tags=["search"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/search")
async def search_filings(
    session: Session,
    q: Annotated[str, Query(min_length=2, max_length=500)],
    tickers: Annotated[list[str] | None, Query()] = None,
    sections: Annotated[list[str] | None, Query()] = None,
    mode: SearchMode = "hybrid",
    rerank: bool = True,
    top_k: Annotated[int, Query(ge=1, le=20)] = 8,
) -> list[RetrievedChunk]:
    filters = SearchFilters(tickers=tickers, sections=sections)
    return await search(session, q, filters, top_k=top_k, mode=mode, rerank=rerank)


@router.get("/filings")
async def filings(session: Session, ticker: str | None = None) -> list[FilingSummary]:
    return await list_filings(session, ticker)
