"""Hybrid retrieval over filing chunks: pgvector + Postgres full-text, fused with RRF,
optionally reranked by a cross-encoder. Every result carries a citation."""

import asyncio
from collections.abc import Sequence
from datetime import date
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import ColumnElement, Select, Text, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Chunk, Filing
from app.rag.embed import get_embedder
from app.rag.rerank import get_reranker

SearchMode = Literal["vector", "keyword", "hybrid"]

CANDIDATES_PER_RETRIEVER = 30
RERANK_POOL = 30  # cross-encoder cost is linear in this; ~1-2s on CPU for 30 pairs
RRF_K = 60  # standard constant from Cormack et al. (2009)


class SearchFilters(BaseModel):
    tickers: list[str] | None = None
    sections: list[str] | None = None
    forms: list[str] | None = None
    since: date | None = None  # filings with report_date on/after this date


class Citation(BaseModel):
    chunk_id: int
    ticker: str
    form: str
    report_date: date | None
    section: str
    url: str


class RetrievedChunk(BaseModel):
    citation: Citation
    text: str
    score: float
    vector_rank: int | None = None  # 1-based rank in each retriever, None if not retrieved
    keyword_rank: int | None = None


def filter_clauses(filters: SearchFilters) -> list[ColumnElement[bool]]:
    clauses: list[ColumnElement[bool]] = []
    if filters.tickers:
        clauses.append(Filing.ticker.in_([t.upper() for t in filters.tickers]))
    if filters.sections:
        clauses.append(Chunk.section.in_(filters.sections))
    if filters.forms:
        clauses.append(Filing.form.in_(filters.forms))
    if filters.since:
        clauses.append(Filing.report_date >= filters.since)
    return clauses


def _base_query(filters: SearchFilters) -> Select[int]:
    return (
        select(Chunk.id).join(Filing, Chunk.filing_id == Filing.id).where(*filter_clauses(filters))
    )


def reciprocal_rank_fusion(rankings: Sequence[Sequence[int]], k: int = RRF_K) -> list[int]:
    """Fuse ranked id lists; ties keep first-seen order so results are deterministic."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda cid: scores[cid], reverse=True)


async def vector_search(
    session: AsyncSession, query_vector: list[float], filters: SearchFilters, limit: int
) -> list[int]:
    # With filters, HNSW's plain scan can return fewer than `limit` rows; pgvector >= 0.8
    # keeps scanning until enough rows pass the WHERE clause.
    await session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    stmt = _base_query(filters).order_by(Chunk.embedding.cosine_distance(query_vector)).limit(limit)
    return list((await session.scalars(stmt)).all())


async def keyword_search(
    session: AsyncSession, query: str, filters: SearchFilters, limit: int
) -> list[int]:
    # plainto_tsquery ANDs every term, which misses most chunks for natural-language
    # questions; OR the terms instead and let ts_rank_cd reward chunks matching more of them.
    tsquery = func.to_tsquery(
        "english", func.replace(cast(func.plainto_tsquery("english", query), Text), "&", "|")
    )
    stmt = (
        _base_query(filters)
        .where(Chunk.tsv.op("@@")(tsquery))
        .order_by(func.ts_rank_cd(Chunk.tsv, tsquery).desc())
        .limit(limit)
    )
    return list((await session.scalars(stmt)).all())


class FilingSummary(BaseModel):
    ticker: str
    form: str
    report_date: date | None
    filing_date: date
    accession_no: str
    url: str
    sections: list[str]
    chunks: int


async def _load(session: AsyncSession, ids: list[int]) -> dict[int, tuple[Chunk, Filing]]:
    rows = await session.execute(
        select(Chunk, Filing).join(Filing, Chunk.filing_id == Filing.id).where(Chunk.id.in_(ids))
    )
    return {chunk.id: (chunk, filing) for chunk, filing in rows}


def _citation(chunk: Chunk, filing: Filing) -> Citation:
    return Citation(
        chunk_id=chunk.id,
        ticker=filing.ticker,
        form=filing.form,
        report_date=filing.report_date,
        section=chunk.section,
        url=filing.url,
    )


async def get_chunk(session: AsyncSession, chunk_id: int) -> RetrievedChunk | None:
    row = (await _load(session, [chunk_id])).get(chunk_id)
    if row is None:
        return None
    chunk, filing = row
    return RetrievedChunk(citation=_citation(chunk, filing), text=chunk.text, score=1.0)


async def list_filings(session: AsyncSession, ticker: str | None = None) -> list[FilingSummary]:
    stmt = (
        select(
            Filing,
            func.array_agg(func.distinct(Chunk.section)),
            func.count(Chunk.id),
        )
        .join(Chunk, Chunk.filing_id == Filing.id)
        .group_by(Filing.id)
        .order_by(Filing.ticker, Filing.filing_date.desc())
    )
    if ticker:
        stmt = stmt.where(Filing.ticker == ticker.upper())
    return [
        FilingSummary(
            ticker=f.ticker,
            form=f.form,
            report_date=f.report_date,
            filing_date=f.filing_date,
            accession_no=f.accession_no,
            url=f.url,
            sections=sorted(sections),
            chunks=count,
        )
        for f, sections, count in await session.execute(stmt)
    ]


async def search(
    session: AsyncSession,
    query: str,
    filters: SearchFilters | None = None,
    *,
    top_k: int = 8,
    mode: SearchMode = "hybrid",
    rerank: bool = True,
) -> list[RetrievedChunk]:
    filters = filters or SearchFilters()

    vector_ids: list[int] = []
    keyword_ids: list[int] = []
    if mode in ("vector", "hybrid"):
        query_vector = await asyncio.to_thread(get_embedder().embed_query, query)
        vector_ids = await vector_search(session, query_vector, filters, CANDIDATES_PER_RETRIEVER)
    if mode in ("keyword", "hybrid"):
        keyword_ids = await keyword_search(session, query, filters, CANDIDATES_PER_RETRIEVER)

    ranked = reciprocal_rank_fusion([r for r in (vector_ids, keyword_ids) if r])
    if not ranked:
        return []
    candidates = ranked[:RERANK_POOL] if rerank else ranked[:top_k]
    rows = await _load(session, candidates)

    scores: list[float]
    if rerank:
        texts = [rows[cid][0].text for cid in candidates]
        scores = await asyncio.to_thread(get_reranker().score, query, texts)
        order = sorted(range(len(candidates)), key=lambda i: scores[i], reverse=True)[:top_k]
        candidates = [candidates[i] for i in order]
        scores = [scores[i] for i in order]
    else:
        # Expose the fused RRF score so callers see a comparable number.
        fused = {cid: 1.0 / (RRF_K + r) for r, cid in enumerate(ranked, start=1)}
        scores = [fused[cid] for cid in candidates]

    vector_rank = {cid: r for r, cid in enumerate(vector_ids, start=1)}
    keyword_rank = {cid: r for r, cid in enumerate(keyword_ids, start=1)}
    results = []
    for cid, score in zip(candidates, scores, strict=True):
        chunk, filing = rows[cid]
        results.append(
            RetrievedChunk(
                citation=_citation(chunk, filing),
                text=chunk.text,
                score=float(score),
                vector_rank=vector_rank.get(cid),
                keyword_rank=keyword_rank.get(cid),
            )
        )
    return results
