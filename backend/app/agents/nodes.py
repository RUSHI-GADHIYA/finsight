"""LangGraph nodes: supervisor -> (filings || market) -> analyst <-> critic -> human_review.

Only three nodes call an LLM (supervisor, analyst, critic). Retrieval and figures come
from MCP tools, and the critic checks citations and numbers deterministically before it
spends a token on judging the rest.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from langgraph.graph import END
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt

from app.agents.llm import AgentLLM
from app.agents.state import (
    Claim,
    ClaimVerdict,
    Critique,
    Figure,
    Report,
    ReportContext,
    ResearchPlan,
    ResearchState,
)
from app.agents.tools import ResearchTools, ToolCallError
from app.guardrails.injection import InjectionGuard
from app.rag.financials import FinancialYear
from app.rag.market import PriceSummary
from app.rag.retrieval import RetrievedChunk

MAX_TICKERS = 4
MAX_SEARCHES = 6  # each search reranks on CPU (~2-4s), so this bounds latency too
TOP_K_PER_SEARCH = 4
MAX_EVIDENCE = 24  # passages sent to the analyst; bounds the smart model's input cost
PASSAGE_CHARS = 1200
FINANCIAL_YEARS = 3
PRICE_PERIOD = "2y"

DISCLAIMER = "For research and education only; not investment advice."


class ReportStore(Protocol):
    async def save(
        self,
        *,
        thread_id: str,
        question: str,
        report: Report,
        warnings: list[str],
        cost_usd: float,
        context: ReportContext,
    ) -> int: ...


@dataclass
class AgentDeps:
    """Run-scoped dependencies, passed as LangGraph runtime context."""

    tools: ResearchTools
    llm: AgentLLM
    store: ReportStore
    thread_id: str
    max_revisions: int = 2
    guard: InjectionGuard | None = None  # screens retrieved passages; None = off


Ctx = Runtime[AgentDeps]


def _started(runtime: Ctx, node: str) -> None:
    runtime.stream_writer({"type": "node_started", "node": node})


# --------------------------------------------------------------------------- supervisor

SUPERVISOR_INSTRUCTIONS = """\
You are the research supervisor of FinSight, a copilot over SEC 10-K filings.
Turn the user's question into a research plan.
- tickers: only tickers from the AVAILABLE COMPANIES list; map company names to tickers. \
Use at most 4. If none of the companies asked about are available, return an empty list.
- filing_queries: 1-3 short search queries for the filing text (risks, strategy, MD&A \
commentary). Do not include company names; filtering by ticker is done separately.
- sections: restrict to the relevant sections, or leave empty to search all.
- need_financials: true if revenue, profit, margins or growth matter to the answer.
- need_prices: true only if share-price performance is asked about."""


async def supervisor(state: ResearchState, runtime: Ctx) -> Command[Any]:
    _started(runtime, "supervisor")
    deps = runtime.context
    try:
        filings = await deps.tools.list_filings()
    except ToolCallError as exc:
        return Command(
            update={
                "status": "no_data",
                "message": "Filing index unavailable",
                "errors": [str(exc)],
            },
            goto=END,
        )
    available: dict[str, set[str]] = {}
    for f in filings:
        available.setdefault(f.ticker, set()).add(str(f.report_date or f.filing_date))
    catalog = "\n".join(
        f"{t}: fiscal years ending {', '.join(sorted(d))}" for t, d in sorted(available.items())
    )

    plan, cost = await deps.llm.parse(
        node="supervisor",
        tier="fast",
        instructions=f"{SUPERVISOR_INSTRUCTIONS}\n\nAVAILABLE COMPANIES:\n{catalog}",
        input=state["question"],
        schema=ResearchPlan,
        max_output_tokens=800,
        writer=runtime.stream_writer,
    )
    tickers = list(dict.fromkeys(t.upper() for t in plan.tickers if t.upper() in available))
    plan = plan.model_copy(update={"tickers": tickers[:MAX_TICKERS]})
    if not plan.tickers:
        message = (
            "None of the companies in the question have filings ingested. "
            f"Available: {', '.join(sorted(available)) or 'none'}."
        )
        return Command(
            update={"plan": plan, "status": "no_data", "message": message, "cost_usd": cost},
            goto=END,
        )
    return Command(
        update={"plan": plan, "status": "running", "cost_usd": cost, "revisions": 0},
        goto=["filings", "market"],  # parallel branches, joined before the analyst
    )


# ------------------------------------------------------------------------------ workers


def _round_robin(lists: Iterable[list[RetrievedChunk]]) -> list[RetrievedChunk]:
    """Interleave results so every (query, ticker) search is represented in the cap."""
    queues = [list(x) for x in lists]
    out: list[RetrievedChunk] = []
    seen: set[int] = set()
    while any(queues):
        for q in queues:
            if q:
                chunk = q.pop(0)
                if chunk.citation.chunk_id not in seen:
                    seen.add(chunk.citation.chunk_id)
                    out.append(chunk)
    return out


async def filings(state: ResearchState, runtime: Ctx) -> dict[str, Any]:
    _started(runtime, "filings")
    plan = state["plan"]
    queries = plan.filing_queries or [state["question"]]
    # Query-major order, so a cap on searches drops later queries, not whole companies.
    searches = [(q, t) for q in queries for t in plan.tickers][:MAX_SEARCHES]
    results: list[list[RetrievedChunk]] = []
    errors: list[str] = []
    for query, ticker in searches:
        try:
            results.append(
                await runtime.context.tools.search(
                    query, [ticker], list(plan.sections), TOP_K_PER_SEARCH
                )
            )
        except ToolCallError as exc:
            errors.append(str(exc))
    evidence = _round_robin(results)[:MAX_EVIDENCE]

    # Filing text is untrusted input too: drop passages that read like instructions to the
    # model before the analyst ever sees them.
    guard = runtime.context.guard
    if guard is not None and evidence:
        flags = await guard.flagged([c.text for c in evidence])
        kept = []
        for chunk, score in zip(evidence, flags, strict=True):
            if score is None:
                kept.append(chunk)
                continue
            cit = chunk.citation
            errors.append(
                f"Dropped passage #{cit.chunk_id} ({cit.ticker} {cit.section}): it reads like "
                "instructions to an AI, a possible prompt injection"
            )
            runtime.stream_writer(
                {
                    "type": "guardrail",
                    "kind": "passage_dropped",
                    "chunk_id": cit.chunk_id,
                    "ticker": cit.ticker,
                    "score": round(score, 4),
                }
            )
        evidence = kept
    return {"evidence": evidence, "errors": errors}


async def market(state: ResearchState, runtime: Ctx) -> dict[str, Any]:
    _started(runtime, "market")
    plan = state["plan"]
    tools = runtime.context.tools
    financials: dict[str, list[FinancialYear]] = {}
    prices: dict[str, PriceSummary] = {}
    errors: list[str] = []
    for ticker in plan.tickers:
        if plan.need_financials:
            try:
                financials[ticker] = await tools.financials(ticker, FINANCIAL_YEARS)
            except ToolCallError as exc:
                errors.append(f"financials unavailable for {ticker}: {exc}")
        if plan.need_prices:
            try:
                prices[ticker] = await tools.prices(ticker, PRICE_PERIOD)
            except ToolCallError as exc:
                errors.append(f"price data unavailable for {ticker}: {exc}")
    return {"financials": financials, "prices": prices, "errors": errors}


# ---------------------------------------------------------------------- figure lookup


def _musd(v: float | None) -> float | None:
    return None if v is None else round(v / 1e6, 1)


def _pct(v: float | None) -> float | None:
    return None if v is None else round(v * 100, 1)


def figure_table(state: ResearchState) -> dict[tuple[str, str, str], float]:
    """(ticker, fiscal_year_end, metric) -> value, in the units the analyst is shown."""
    table: dict[tuple[str, str, str], float] = {}
    for ticker, years in state.get("financials", {}).items():
        for y in years:
            fye = y.fiscal_year_end.isoformat()
            row = {
                "revenue_musd": _musd(y.revenue),
                "gross_profit_musd": _musd(y.gross_profit),
                "operating_income_musd": _musd(y.operating_income),
                "net_income_musd": _musd(y.net_income),
                "gross_margin_pct": _pct(y.gross_margin),
                "operating_margin_pct": _pct(y.operating_margin),
                "net_margin_pct": _pct(y.net_margin),
            }
            table.update({(ticker, fye, m): v for m, v in row.items() if v is not None})
    for ticker, p in state.get("prices", {}).items():
        table[(ticker, "", "price_return_pct")] = round(p.total_return * 100, 1)
    return table


def figure_matches(fig: Figure, table: dict[tuple[str, str, str], float]) -> bool:
    expected = table.get((fig.ticker.upper(), fig.fiscal_year_end, fig.metric))
    if expected is None:
        return False
    if fig.metric.endswith("_pct"):
        return abs(fig.value - expected) <= 0.15  # rounding to one decimal
    return math.isclose(fig.value, expected, rel_tol=0.005, abs_tol=0.5)


# ------------------------------------------------------------------------------ analyst

ANALYST_INSTRUCTIONS = f"""\
You are FinSight's analyst. Write a concise research brief answering the question, using \
ONLY the filing passages and tables provided.
Rules:
- Every claim must cite the passages that support it in chunk_ids (the numbers in \
[chunk N]) and/or list the table figures it uses in figures. Do not cite anything else.
- Copy figures exactly as shown in the tables, same units ($ millions or percent), with \
the ticker and fiscal_year_end of the row (use '' for price returns).
- Never invent numbers, dates or facts. If the sources don't cover part of the question, \
say so in the summary instead of guessing.
- Compare companies side by side when several are asked about. 2-5 sections, each with \
1-5 claims of one or two sentences.
- The summary is 2-4 sentences. Do not give buy/sell recommendations ({DISCLAIMER})."""


def format_evidence(evidence: list[RetrievedChunk]) -> str:
    blocks = []
    for c in evidence:
        cit = c.citation
        blocks.append(
            f"[chunk {cit.chunk_id}] {cit.ticker} {cit.form} fiscal year ending "
            f"{cit.report_date} | {cit.section}\n{c.text[:PASSAGE_CHARS]}"
        )
    return "\n\n".join(blocks) or "(no passages found)"


def format_tables(state: ResearchState) -> str:
    rows = []
    for ticker, years in state.get("financials", {}).items():
        for y in years:
            cells = [
                f"revenue_musd={_musd(y.revenue)}",
                f"gross_profit_musd={_musd(y.gross_profit)}",
                f"operating_income_musd={_musd(y.operating_income)}",
                f"net_income_musd={_musd(y.net_income)}",
                f"gross_margin_pct={_pct(y.gross_margin)}",
                f"operating_margin_pct={_pct(y.operating_margin)}",
                f"net_margin_pct={_pct(y.net_margin)}",
            ]
            rows.append(f"{ticker} fiscal_year_end={y.fiscal_year_end}: {' '.join(cells)}")
    for ticker, p in state.get("prices", {}).items():
        rows.append(
            f"{ticker} fiscal_year_end='': price_return_pct={round(p.total_return * 100, 1)} "
            f"over {p.period} (close {p.start_close} -> {p.end_close}; {p.source})"
        )
    return "\n".join(rows) or "(no tables requested)"


async def analyst(state: ResearchState, runtime: Ctx) -> dict[str, Any]:
    _started(runtime, "analyst")
    revising = "critique" in state and "report" in state
    parts = [
        f"FILING PASSAGES:\n{format_evidence(state.get('evidence', []))}",
        f"FINANCIAL TABLES (SEC XBRL, annual 10-K):\n{format_tables(state)}",
    ]
    if state.get("errors"):
        parts.append("DATA GAPS:\n" + "\n".join(state["errors"]))
    if revising:
        rejected = "\n".join(
            f"- claim {v.claim_id}: {v.reason}" for v in state["critique"].unsupported()
        )
        parts.append(
            f"YOUR PREVIOUS DRAFT:\n{state['report'].model_dump_json()}\n\n"
            "A fact-checker rejected these claims (claim 0 is the summary; the others are "
            "numbered in reading order from 1):\n"
            f"{rejected}\nFix them using the sources, or remove them. Keep the rest."
        )
    parts.append(f"QUESTION:\n{state['question']}")  # per-run data last

    report, cost = await runtime.context.llm.parse(
        node="analyst",
        tier="smart",
        instructions=ANALYST_INSTRUCTIONS,
        input="\n\n".join(parts),
        schema=Report,
        max_output_tokens=8000,
        writer=runtime.stream_writer,
    )
    return {
        "report": report,
        "cost_usd": cost,
        "revisions": state.get("revisions", 0) + (1 if revising else 0),
    }


# ------------------------------------------------------------------------------- critic

CRITIC_INSTRUCTIONS = """\
You are a strict fact-checker for a research report. For each numbered claim, decide \
whether its sources fully support it. Claim 0 is the report summary: it must not contradict \
or go beyond the other claims and the sources.
- Passages: a claim is unsupported if it adds facts, numbers, dates or causal links the \
cited passages do not state, or overstates them. Paraphrase is fine.
- Table figures: the numbers themselves are already verified. Check that the WORDS describe \
them correctly: direction of change (rose/fell, expanded/contracted), comparisons, periods.
Return one verdict per claim, with a short reason."""

SUMMARY_ID = 0  # the summary is judged as claim 0; body claims are numbered from 1


def check_citations(
    claims: list[Claim], evidence_ids: set[int], table: dict[tuple[str, str, str], float]
) -> dict[int, ClaimVerdict]:
    """Free, deterministic checks. Returns failures only, keyed by 1-based claim id."""
    failures: dict[int, ClaimVerdict] = {}
    for i, claim in enumerate(claims, start=1):
        unknown = [cid for cid in claim.chunk_ids if cid not in evidence_ids]
        wrong = [f for f in claim.figures if not figure_matches(f, table)]
        reason = None
        if not claim.chunk_ids and not claim.figures:
            reason = "no source cited"
        elif unknown:
            reason = f"cites passages that were not retrieved: {unknown}"
        elif wrong:
            reason = "figures do not match the tables: " + "; ".join(
                f"{f.ticker} {f.metric} {f.fiscal_year_end or '(price)'} = {f.value}" for f in wrong
            )
        if reason:
            failures[i] = ClaimVerdict(claim_id=i, supported=False, reason=reason)
    return failures


def judge_input(report: Report, skip: set[int], state: ResearchState) -> tuple[set[int], str]:
    """The judge's view: the summary + claims not in `skip`, with exactly the passages and
    table rows they rely on. Returns (claim ids to judge, prompt input)."""
    evidence = {c.citation.chunk_id: c for c in state.get("evidence", [])}
    claims = [(i, c) for i, c in enumerate(report.claims(), start=1) if i not in skip]
    cited = sorted({cid for _, c in claims for cid in c.chunk_ids if cid in evidence})
    lines = [f"claim {SUMMARY_ID} (summary): {report.summary}"]
    for i, c in claims:
        figures = "; ".join(
            f"{f.ticker} {f.metric} {f.fiscal_year_end or '(price)'} = {f.value}" for f in c.figures
        )
        lines.append(f"claim {i} (passages {c.chunk_ids}; figures [{figures}]): {c.text}")
    parts = [f"PASSAGES:\n{format_evidence([evidence[cid] for cid in cited])}"]
    if any(c.figures for _, c in claims):
        parts.append(f"TABLES (SEC XBRL, annual 10-K):\n{format_tables(state)}")
    parts.append("CLAIMS:\n" + "\n".join(lines))
    return {SUMMARY_ID} | {i for i, _ in claims}, "\n\n".join(parts)


async def critic(state: ResearchState, runtime: Ctx) -> Command[Literal["analyst", "human_review"]]:
    _started(runtime, "critic")
    deps = runtime.context
    report = state["report"]
    claims = report.claims()
    evidence_ids = {c.citation.chunk_id for c in state.get("evidence", [])}
    verdicts = check_citations(claims, evidence_ids, figure_table(state))
    can_revise = state.get("revisions", 0) < deps.max_revisions

    cost = 0.0
    # Free checks already force a revision: don't pay for a judge call on a draft that
    # is about to change. Otherwise judge everything that passed them, plus the summary.
    if not (verdicts and can_revise):
        judged_ids, judge_text = judge_input(report, set(verdicts), state)
        judged, cost = await deps.llm.parse(
            node="critic",
            tier="fast",
            instructions=CRITIC_INSTRUCTIONS,
            input=judge_text,
            schema=Critique,
            max_output_tokens=2000,
            writer=runtime.stream_writer,
        )
        verdicts.update({v.claim_id: v for v in judged.verdicts if v.claim_id in judged_ids})

    critique = Critique(
        verdicts=[
            verdicts.get(i) or ClaimVerdict(claim_id=i, supported=True, reason="not flagged")
            for i in range(SUMMARY_ID, len(claims) + 1)
        ]
    )
    unsupported = critique.unsupported()
    update: dict[str, Any] = {"critique": critique, "cost_usd": cost}
    if unsupported and can_revise:
        return Command(update=update, goto="analyst")

    update["warnings"] = [
        f"{'Summary' if v.claim_id == SUMMARY_ID else f'Claim {v.claim_id}'} may be "
        f"unsupported: {v.reason}"
        for v in unsupported
    ]
    update["status"] = "awaiting_approval"
    return Command(update=update, goto="human_review")


# ------------------------------------------------------------------------- human review


def report_context(state: ResearchState, report: Report) -> ReportContext:
    cited = {cid for claim in report.claims() for cid in claim.chunk_ids}
    return ReportContext(
        sources=[c for c in state.get("evidence", []) if c.citation.chunk_id in cited],
        financials=state.get("financials", {}),
        prices=state.get("prices", {}),
        errors=state.get("errors", []),
    )


async def human_review(state: ResearchState, runtime: Ctx) -> dict[str, Any]:
    # Pauses the run; the API resumes it with Command(resume={"action": ..., ...}).
    # On resume LangGraph re-runs this node from the top, and interrupt() returns the value.
    decision: dict[str, Any] = interrupt(
        {
            "question": state["question"],
            "report": state["report"].model_dump(mode="json"),
            "context": report_context(state, state["report"]).model_dump(mode="json"),
            "warnings": state.get("warnings", []),
            "cost_usd": round(state.get("cost_usd", 0.0), 6),
            "disclaimer": DISCLAIMER,
        }
    )
    action = decision.get("action")
    if action == "reject":
        return {"status": "rejected"}
    report = state["report"]
    if action == "edit":
        report = Report.model_validate(decision["report"])
    elif action != "approve":
        raise ValueError(f"Unknown review action: {action!r}")

    deps = runtime.context
    report_id = await deps.store.save(
        thread_id=deps.thread_id,
        question=state["question"],
        report=report,
        warnings=state.get("warnings", []),
        cost_usd=state.get("cost_usd", 0.0),
        context=report_context(state, report),
    )
    return {"report": report, "status": "approved", "report_id": report_id}
