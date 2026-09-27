"""Generate a small golden set: one analyst-style question per sampled filing chunk.

The only LLM spend in Week 2. Hard-capped by --budget-usd; the output is committed and
not regenerated unless --force is passed.

    uv run python -m evals.generate_golden            # 25 questions, <= $0.25
"""

import argparse
import asyncio
import sys
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import Chunk, Filing
from app.db.session import get_sessionmaker
from app.llm import get_openai_client
from app.observability.cost import BudgetExceeded, CostTracker

OUT = Path(__file__).parent / "golden_set.jsonl"
MAX_PASSAGE_CHARS = 1500
PER_TICKER = 3  # candidates per company; extra ones cover passages judged unanswerable

INSTRUCTIONS = """\
You write evaluation questions for a search engine over SEC 10-K filings.
Given one passage, write ONE specific question an equity analyst might ask that this passage \
answers. Name the company. Paraphrase instead of copying phrases from the passage, so keyword \
overlap alone is not enough to find it. If the passage is boilerplate, a table of contents, or \
too generic to support a specific question, set answerable to false."""


class GeneratedQuestion(BaseModel):
    question: str = Field(description="One specific question answered by the passage")
    answerable: bool


class GoldenItem(BaseModel):
    id: int
    question: str
    source_chunk_id: int
    ticker: str
    section: str
    fiscal_year_end: str | None


async def sample_chunks(seed: int) -> list[tuple[Chunk, Filing]]:
    """Deterministic, stratified sample: a few non-trivial chunks per ticker, mixed sections."""
    shuffle = func.md5(func.concat(Chunk.id, str(seed)))
    ranked = (
        select(
            Chunk.id,
            func.row_number().over(partition_by=Filing.ticker, order_by=shuffle).label("rn"),
        )
        .join(Filing, Chunk.filing_id == Filing.id)
        .where(Chunk.chunk_index > 0, func.length(Chunk.text) > 600)
        .subquery()
    )
    stmt = (
        select(Chunk, Filing)
        .join(Filing, Chunk.filing_id == Filing.id)
        .join(ranked, ranked.c.id == Chunk.id)
        .where(ranked.c.rn <= PER_TICKER)
        .order_by(ranked.c.rn, Filing.ticker)  # round-robin across tickers
    )
    async with get_sessionmaker()() as session:
        return [(c, f) for c, f in await session.execute(stmt)]


async def generate(n: int, budget_usd: float, seed: int) -> tuple[list[GoldenItem], CostTracker]:
    settings = get_settings()
    client = get_openai_client()
    tracker = CostTracker(budget_usd=budget_usd)
    items: list[GoldenItem] = []

    for chunk, filing in await sample_chunks(seed):
        if len(items) >= n:
            break
        passage = (
            f"Company: {filing.ticker} | Form {filing.form} | fiscal year ending "
            f"{filing.report_date} | section: {chunk.section}\n\n{chunk.text[:MAX_PASSAGE_CHARS]}"
        )
        resp = await client.responses.parse(
            model=settings.llm_fast_model,
            instructions=INSTRUCTIONS,
            input=passage,
            text_format=GeneratedQuestion,
            reasoning={"effort": "minimal"},
            max_output_tokens=300,
        )
        tracker.record(settings.llm_fast_model, resp.usage)  # raises past the budget
        parsed = resp.output_parsed
        if parsed is None or not parsed.answerable:
            continue
        items.append(
            GoldenItem(
                id=len(items) + 1,
                question=parsed.question.strip(),
                source_chunk_id=chunk.id,
                ticker=filing.ticker,
                section=chunk.section,
                fiscal_year_end=str(filing.report_date) if filing.report_date else None,
            )
        )
        print(f"[{len(items):>2}/{n}] {filing.ticker} {chunk.section}: {items[-1].question}")
    return items, tracker


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=25)
    parser.add_argument("--budget-usd", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--force", action="store_true", help="overwrite existing golden set")
    args = parser.parse_args()

    if OUT.exists() and not args.force:
        sys.exit(f"{OUT.name} already exists; pass --force to regenerate (costs API credits)")

    try:
        items, tracker = asyncio.run(generate(args.n, args.budget_usd, args.seed))
    except BudgetExceeded as exc:
        sys.exit(f"Stopped: {exc}")

    OUT.write_text("".join(item.model_dump_json() + "\n" for item in items), encoding="utf-8")
    print(f"\nWrote {len(items)} questions to {OUT}")
    print(f"LLM cost: {tracker.summary()}")


if __name__ == "__main__":
    main()
