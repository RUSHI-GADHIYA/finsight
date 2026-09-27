"""Retrieval eval over the golden set. No LLM calls: free to run as often as needed.

For each question, did the source chunk come back? Compares vector-only, hybrid (RRF) and
hybrid + cross-encoder rerank on Hit@5 and MRR@10. Searches are filtered to the question's
ticker, as the agents will do. Exits non-zero if hybrid+rerank falls below thresholds.toml.

    uv run python -m evals.run_retrieval_evals
    uv run python -m evals.run_retrieval_evals --thresholds strict.toml   # prove the gate fails
"""

import argparse
import asyncio
import json
import os
import sys
import time
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.db.session import get_sessionmaker
from app.rag.retrieval import SearchFilters, SearchMode, search

HERE = Path(__file__).parent
CONFIGS: list[tuple[str, SearchMode, bool]] = [
    ("vector", "vector", False),
    ("hybrid (RRF)", "hybrid", False),
    ("hybrid + rerank", "hybrid", True),
]
TOP_K = 10


@dataclass
class Scores:
    hit_at_5: float
    mrr_at_10: float
    seconds_per_query: float


async def evaluate(golden: list[dict[str, object]], mode: SearchMode, rerank: bool) -> Scores:
    hits, reciprocal_ranks = 0, 0.0
    start = time.perf_counter()
    for item in golden:
        async with get_sessionmaker()() as session:
            results = await search(
                session,
                str(item["question"]),
                SearchFilters(tickers=[str(item["ticker"])]),
                top_k=TOP_K,
                mode=mode,
                rerank=rerank,
            )
        ids = [r.citation.chunk_id for r in results]
        if item["source_chunk_id"] in ids:
            rank = ids.index(item["source_chunk_id"]) + 1
            hits += rank <= 5
            reciprocal_ranks += 1.0 / rank
    n = len(golden)
    return Scores(hits / n, reciprocal_ranks / n, (time.perf_counter() - start) / n)


async def run(configs: list[tuple[str, SearchMode, bool]]) -> dict[str, Scores]:
    golden = [
        json.loads(line)
        for line in (HERE / "golden_set.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    # Warm up model loading so the first config's latency isn't inflated.
    async with get_sessionmaker()() as session:
        await search(session, "warm up", top_k=1, rerank=True)
    results = {}
    for name, mode, rerank in configs:
        results[name] = await evaluate(golden, mode, rerank)
        print(f"{name:16} Hit@5={results[name].hit_at_5:.2f} MRR@10={results[name].mrr_at_10:.2f}")
    return results


def write_report(results: dict[str, Scores], n: int) -> Path:
    lines = [
        "# Retrieval eval",
        "",
        f"{n} generated questions over 12 companies' 10-Ks, filtered to the question's ticker; "
        f"run {date.today()}. Hit@5: source passage in the top 5. MRR@10: mean reciprocal rank.",
        "",
        "| Config | Hit@5 | MRR@10 | Latency / query (CPU) |",
        "|---|---|---|---|",
        *(
            f"| {name} | {s.hit_at_5:.2f} | {s.mrr_at_10:.2f} | {s.seconds_per_query:.2f}s |"
            for name, s in results.items()
        ),
        "",
    ]
    out = HERE / "results" / "retrieval.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    # Machine-readable copy for the API's /metrics page.
    summary = {
        "run_date": str(date.today()),
        "questions": n,
        "configs": {
            name: {
                "hit_at_5": round(s.hit_at_5, 4),
                "mrr_at_10": round(s.mrr_at_10, 4),
                "seconds_per_query": round(s.seconds_per_query, 3),
            }
            for name, s in results.items()
        },
    }
    out.with_suffix(".json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return out


GATED = "hybrid + rerank"  # the configuration the agents use


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--thresholds", type=Path, default=HERE / "thresholds.toml")
    parser.add_argument(
        "--gated-only",
        action="store_true",
        help=f"evaluate only '{GATED}' (the CI gate), skipping the ablation configs",
    )
    args = parser.parse_args()

    configs = [c for c in CONFIGS if c[0] == GATED] if args.gated_only else CONFIGS
    results = asyncio.run(run(configs))
    n = len((HERE / "golden_set.jsonl").read_text(encoding="utf-8").splitlines())
    if not args.gated_only:  # the committed ablation table needs all configs
        print(f"Wrote {write_report(results, n)}")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        rows = [
            f"### Retrieval eval ({n} questions)",
            "",
            "| Config | Hit@5 | MRR@10 |",
            "|---|---|---|",
            *(
                f"| {name} | {sc.hit_at_5:.2f} | {sc.mrr_at_10:.2f} |"
                for name, sc in results.items()
            ),
        ]
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")

    thresholds = tomllib.loads(args.thresholds.read_text(encoding="utf-8"))["retrieval"]
    best = results[GATED]
    if best.hit_at_5 < thresholds["min_hit_at_5"] or best.mrr_at_10 < thresholds["min_mrr_at_10"]:
        sys.exit(
            f"FAIL: {GATED} Hit@5={best.hit_at_5:.2f} MRR@10={best.mrr_at_10:.2f} "
            f"is below the thresholds {thresholds}"
        )
    print(f"PASS: {GATED} meets the thresholds {thresholds}")


if __name__ == "__main__":
    main()
