"""Answer eval: run the full agent graph on golden questions and score the reports.

**Costs API credits** (hard-capped by --budget-usd, actual spend printed). Not run in CI.

Metrics per question (the run stops at the human-review interrupt; nothing is saved):
- evidence hit: the golden source passage was among the passages the filings agent retrieved
- citation hit: the final report cites the golden source passage
- faithfulness: share of claims supported by what they cite, judged by an independent
  fast-model prompt (figure-only claims are checked exactly against the XBRL tables)

    uv run python -m evals.run_answer_evals --n 8 --budget-usd 0.50
"""

import argparse
import asyncio
import json
import sys
import time
import tomllib
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from app.agents.graph import build_graph, checkpoint_serde
from app.agents.llm import OpenAIAgentLLM
from app.agents.nodes import AgentDeps, check_citations, figure_table, judge_input
from app.agents.state import Critique, Report, ResearchState
from app.agents.tools import mcp_tools
from app.config import get_settings
from app.observability.cost import BudgetExceeded, CostTracker

HERE = Path(__file__).parent

JUDGE_INSTRUCTIONS = """\
You grade a research report for faithfulness to its sources. For each numbered claim, \
decide whether its sources support everything it states. Claim 0 is the summary: it must \
not contradict or go beyond the other claims and sources. Unsupported means it adds or \
changes facts, numbers, dates or causal links, misdescribes table figures (e.g. says a \
margin rose when it fell), or overstates certainty. Reasonable paraphrase and summarizing \
are supported. One verdict per claim, with a short reason."""


class NoopStore:
    async def save(self, **kwargs: Any) -> int:
        raise AssertionError("the eval never approves a report")


@dataclass
class Row:
    id: int
    ticker: str
    question: str
    evidence_hit: bool
    citation_hit: bool
    claims: int
    supported: int
    revisions: int
    warnings: int
    cost_usd: float
    seconds: float
    note: str = ""


async def judge(llm: OpenAIAgentLLM, report: Report, state: ResearchState) -> tuple[int, int]:
    """(supported, total) over the summary + claims; free-check failures count as unsupported.

    Same view of the sources as the critic (`judge_input`), but an independent prompt, run on
    the final report.
    """
    claims = report.claims()
    evidence_ids = {c.citation.chunk_id for c in state.get("evidence", [])}
    failed = check_citations(claims, evidence_ids, figure_table(state))
    judged_ids, text = judge_input(report, set(failed), state)
    verdict, _ = await llm.parse(
        node="judge",
        tier="fast",
        instructions=JUDGE_INSTRUCTIONS,
        input=text,
        schema=Critique,
        max_output_tokens=2000,
        writer=lambda _: None,
    )
    supported = sum(1 for v in verdict.verdicts if v.claim_id in judged_ids and v.supported)
    return supported, len(claims) + 1


async def run(golden: list[dict[str, Any]], tracker: CostTracker) -> list[Row]:
    graph = build_graph(InMemorySaver(serde=checkpoint_serde()))
    llm = OpenAIAgentLLM(tracker)
    rows: list[Row] = []
    async with mcp_tools() as tools:
        for item in golden:
            thread_id = uuid.uuid4().hex
            deps = AgentDeps(
                tools=tools,
                llm=llm,
                store=NoopStore(),
                thread_id=thread_id,
                max_revisions=get_settings().max_revisions,
            )
            spent_before, start = tracker.spent_usd, time.perf_counter()
            question_input: ResearchState = {"question": item["question"]}
            state: dict[str, Any] = await graph.ainvoke(
                question_input, {"configurable": {"thread_id": thread_id}}, context=deps
            )
            gold = item["source_chunk_id"]
            report: Report | None = state.get("report")
            row = Row(
                id=item["id"],
                ticker=item["ticker"],
                question=item["question"],
                evidence_hit=any(c.citation.chunk_id == gold for c in state.get("evidence", [])),
                citation_hit=report is not None
                and any(gold in c.chunk_ids for c in report.claims()),
                claims=0,
                supported=0,
                revisions=state.get("revisions", 0),
                warnings=len(state.get("warnings", [])),
                cost_usd=0.0,
                seconds=0.0,
                note=state.get("message", "") if report is None else "",
            )
            if report is not None:
                row.supported, row.claims = await judge(llm, report, state)  # type: ignore[arg-type]
            row.cost_usd = tracker.spent_usd - spent_before
            row.seconds = time.perf_counter() - start
            rows.append(row)
            print(
                f"[{len(rows)}/{len(golden)}] {row.ticker}: evidence={row.evidence_hit} "
                f"cited={row.citation_hit} faithful={row.supported}/{row.claims} "
                f"rev={row.revisions} ${row.cost_usd:.4f} {row.seconds:.0f}s"
            )
    return rows


def write_markdown(rows: list[Row], tracker: CostTracker) -> tuple[float, float]:
    n = len(rows)
    evidence = sum(r.evidence_hit for r in rows) / n
    citation = sum(r.citation_hit for r in rows) / n
    total_claims = sum(r.claims for r in rows)
    faithfulness = sum(r.supported for r in rows) / total_claims if total_claims else 0.0
    settings = get_settings()
    lines = [
        "# Answer eval",
        "",
        f"{n} golden questions through the full agent graph (supervisor → filings + market → "
        f"analyst ⇄ critic), stopped at the human-review step; run {date.today()}. "
        f"Models: {settings.llm_fast_model} (supervisor, critic, judge), "
        f"{settings.llm_smart_model} (analyst).",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Evidence hit (golden passage retrieved by the agent) | {evidence:.2f} |",
        f"| Citation hit (golden passage cited in the report) | {citation:.2f} |",
        f"| Faithfulness (summary + claims supported by their sources, {total_claims} judged) "
        f"| {faithfulness:.2f} |",
        f"| Mean revisions per report | {sum(r.revisions for r in rows) / n:.2f} |",
        f"| Mean cost per question (incl. judge) | ${tracker.spent_usd / n:.4f} |",
        f"| Mean latency per question | {sum(r.seconds for r in rows) / n:.0f}s |",
        "",
        "Citation hit is strict: each question was generated from one passage, and a report "
        "can answer correctly from other passages.",
        "",
        "| # | Ticker | Evidence | Cited | Faithful | Revisions | Cost |",
        "|---|---|---|---|---|---|---|",
        *(
            f"| {r.id} | {r.ticker} | {'✓' if r.evidence_hit else '✗'} | "
            f"{'✓' if r.citation_hit else '✗'} | {r.supported}/{r.claims} | {r.revisions} | "
            f"${r.cost_usd:.4f} |"
            for r in rows
        ),
        "",
    ]
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "answers.md").write_text("\n".join(lines), encoding="utf-8")
    return faithfulness, citation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--n", type=int, default=8)
    parser.add_argument("--budget-usd", type=float, default=0.50)
    args = parser.parse_args()

    golden = [
        json.loads(line)
        for line in (HERE / "golden_set.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ][: args.n]
    tracker = CostTracker(budget_usd=args.budget_usd)
    try:
        rows = asyncio.run(run(golden, tracker))
    except BudgetExceeded as exc:
        sys.exit(f"Stopped: {exc}\nLLM cost: {tracker.summary()}")

    faithfulness, citation = write_markdown(rows, tracker)
    print(f"\nFaithfulness {faithfulness:.2f}, citation hit {citation:.2f}")
    print(f"Wrote {HERE / 'results' / 'answers.md'}")
    print(f"LLM cost: {tracker.summary()}")

    gate = tomllib.loads((HERE / "thresholds.toml").read_text(encoding="utf-8")).get("answers")
    if gate and faithfulness < gate["min_faithfulness"]:
        sys.exit(f"FAIL: faithfulness {faithfulness:.2f} < {gate['min_faithfulness']}")


if __name__ == "__main__":
    main()
