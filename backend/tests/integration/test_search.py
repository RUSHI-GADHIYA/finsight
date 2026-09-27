"""Hybrid search against real Postgres + pgvector.

Fixtures live inside a transaction that is rolled back, so the dev database is untouched.
Skipped automatically when Postgres isn't reachable.
"""

import hashlib
import math
import re
from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import get_settings
from app.db.models import EMBEDDING_DIM, Chunk, Filing
from app.rag import retrieval
from app.rag.retrieval import SearchFilters, get_chunk, list_filings, search

pytestmark = pytest.mark.integration

TICKER = "ZZTEST"
TEXTS = {
    "risk_factors": [
        "Export controls restrict sales of advanced chips to China.",
        "Competition in the graphics market is intense.",
    ],
    "mdna": [
        "Data center revenue grew strongly on demand for accelerators.",
        "Gross margin declined due to inventory provisions.",
    ],
}


class FakeEmbedder:
    """Hashed bag-of-words: texts sharing words get similar vectors."""

    dim = EMBEDDING_DIM

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for word in re.findall(r"[a-z]+", text.lower()):
            v[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


class FakeReranker:
    """Scores by word overlap with the query."""

    def score(self, query: str, texts: list[str]) -> list[float]:
        q = set(re.findall(r"[a-z]+", query.lower()))
        return [float(len(q & set(re.findall(r"[a-z]+", t.lower())))) for t in texts]


@pytest.fixture
async def session(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url)
    try:
        conn = await engine.connect()
    except Exception as exc:  # noqa: BLE001 - any connection failure means "no DB here"
        await engine.dispose()
        pytest.skip(f"Postgres not reachable: {exc}")

    monkeypatch.setattr(retrieval, "get_embedder", FakeEmbedder)
    monkeypatch.setattr(retrieval, "get_reranker", FakeReranker)
    trans = await conn.begin()
    async with AsyncSession(bind=conn, expire_on_commit=False) as s:
        embedder = FakeEmbedder()
        s.add(
            Filing(
                ticker=TICKER,
                cik="0000000000",
                form="10-K",
                accession_no="TEST-0001",
                filing_date=date(2025, 3, 1),
                report_date=date(2025, 1, 31),
                url="https://example.com/10k.htm",
                chunks=[
                    Chunk(section=sec, chunk_index=i, text=t, embedding=embedder._vec(t))
                    for sec, texts in TEXTS.items()
                    for i, t in enumerate(texts)
                ],
            )
        )
        await s.flush()
        yield s
    await trans.rollback()
    await conn.close()
    await engine.dispose()


@pytest.mark.parametrize("mode", ["vector", "keyword", "hybrid"])
async def test_each_mode_finds_the_relevant_chunk(session: AsyncSession, mode: str) -> None:
    results = await search(
        session,
        "export controls on chips to China",
        SearchFilters(tickers=[TICKER]),
        top_k=2,
        mode=mode,  # type: ignore[arg-type]
        rerank=False,
    )
    assert results[0].text.startswith("Export controls")
    assert results[0].citation.ticker == TICKER
    assert results[0].citation.section == "risk_factors"


async def test_rerank_and_section_filter(session: AsyncSession) -> None:
    results = await search(
        session,
        "gross margin inventory",
        SearchFilters(tickers=[TICKER], sections=["mdna"]),
        top_k=5,
        rerank=True,
    )
    assert {r.citation.section for r in results} == {"mdna"}
    assert results[0].text.startswith("Gross margin")
    assert results[0].vector_rank is not None and results[0].keyword_rank is not None


async def test_get_chunk_and_list_filings(session: AsyncSession) -> None:
    hit = (await search(session, "graphics competition", SearchFilters(tickers=[TICKER])))[0]
    chunk = await get_chunk(session, hit.citation.chunk_id)
    assert chunk is not None and chunk.text == hit.text
    assert await get_chunk(session, -1) is None

    [summary] = await list_filings(session, TICKER)
    assert summary.chunks == 4
    assert summary.sections == ["mdna", "risk_factors"]
