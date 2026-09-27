"""EDGAR -> parse -> chunk -> embed -> Postgres.

CLI:  uv run python -m app.rag.ingest NVDA AMD --forms 10-K --limit 2
"""

import argparse
import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Chunk, Filing
from app.db.session import get_sessionmaker
from app.rag.chunker import chunk_text
from app.rag.edgar_client import EdgarClient, FilingRef
from app.rag.embed import Embedder, get_embedder
from app.rag.parser import parse_filing

log = logging.getLogger(__name__)


@dataclass
class IngestResult:
    ticker: str
    accession_no: str
    status: str  # "ingested" | "skipped" | "no_sections"
    chunks: int = 0


async def ingest_filing(
    session: AsyncSession,
    edgar: EdgarClient,
    embedder: Embedder,
    ref: FilingRef,
    reingest: bool = False,
) -> IngestResult:
    existing = await session.scalar(
        select(Filing.id).where(Filing.accession_no == ref.accession_no)
    )
    if existing and not reingest:
        return IngestResult(ref.ticker, ref.accession_no, "skipped")

    html = await edgar.fetch_document(ref.url)
    sections = parse_filing(html)
    if not sections:
        log.warning("No sections parsed from %s", ref.url)
        return IngestResult(ref.ticker, ref.accession_no, "no_sections")

    settings = get_settings()
    pieces = [
        (section.key, idx, text)
        for section in sections
        for idx, text in enumerate(
            chunk_text(section.text, settings.chunk_size_chars, settings.chunk_overlap_chars)
        )
    ]
    # CPU-bound; keep the event loop free.
    vectors = await asyncio.to_thread(embedder.embed_documents, [text for _, _, text in pieces])

    filing = Filing(
        ticker=ref.ticker,
        cik=ref.cik,
        form=ref.form,
        accession_no=ref.accession_no,
        filing_date=ref.filing_date,
        report_date=ref.report_date,
        url=ref.url,
        chunks=[
            Chunk(section=key, chunk_index=idx, text=text, embedding=vec)
            for (key, idx, text), vec in zip(pieces, vectors, strict=True)
        ],
    )
    if existing:
        # Replace in the same transaction, so a failed re-parse never loses the old copy.
        await session.execute(delete(Filing).where(Filing.id == existing))
    session.add(filing)
    await session.commit()
    return IngestResult(ref.ticker, ref.accession_no, "ingested", len(pieces))


async def ingest_tickers(
    tickers: list[str],
    forms: tuple[str, ...] = ("10-K",),
    limit: int = 2,
    reingest: bool = False,
) -> list[IngestResult]:
    results: list[IngestResult] = []
    embedder = get_embedder()
    async with EdgarClient(get_settings().sec_user_agent) as edgar:
        for ticker in tickers:
            for ref in await edgar.list_filings(ticker, forms, limit):
                async with get_sessionmaker()() as session:
                    result = await ingest_filing(session, edgar, embedder, ref, reingest)
                log.info("%s %s %s chunks=%d", *vars(result).values())
                results.append(result)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest SEC filings into the vector store")
    parser.add_argument("tickers", nargs="+")
    parser.add_argument("--forms", nargs="+", default=["10-K"])
    parser.add_argument("--limit", type=int, default=2, help="filings per ticker")
    parser.add_argument(
        "--reingest", action="store_true", help="re-parse filings already in the DB"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for noisy in ("httpx", "sentence_transformers", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    tickers = [t.upper() for t in args.tickers]
    asyncio.run(ingest_tickers(tickers, tuple(args.forms), args.limit, args.reingest))


if __name__ == "__main__":
    main()
