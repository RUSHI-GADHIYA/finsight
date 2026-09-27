# FinSight — Multi-Agent Financial Research Copilot

Ask a question like *"Compare NVIDIA and AMD's data-center risk factors and margins over the
last 2 years"* and a team of AI agents researches SEC filings and market data, writes a cited
analyst brief, fact-checks itself, and waits for your approval.

> Status: **Week 4 of 5 done**. Ask a question in the UI, watch the agents work live, read
> a cited report with a margins chart, then edit and sign off. Around that: prompt-injection
> guardrails (on questions *and* retrieved filing text), PII redaction, an audit log, a
> semantic cache of signed-off reports, per-run cost/latency metrics, and Langfuse tracing.
> It runs over 24 real 10-Ks (12 companies, ~4.4k passages), and a report costs about 2-4 cents.

![A signed-off FinSight report: red tick marks for verified claims, margins chart from SEC XBRL](docs/report.jpg)

## Architecture

```
Next.js ──SSE──> FastAPI  /research, /research/{id}/resume, /reports
                   │
          LangGraph graph ── Postgres checkpointer (pause for approval, resume after restarts)

   supervisor ──┬─> filings ──┬─> analyst ──> critic ──> human_review (interrupt) ──> saved report
    (plan)      └─> market  ──┘      ^           │
                                     └───────────┘  unsupported claims → revise (max 2)
                      │                   │
                      └──── MCP client ───┴──> finsight-mcp: search_filings, get_financials,
                                                get_price_history, list_filings, get_chunk
                                                      │
       pgvector + Postgres full-text (RRF) + cross-encoder rerank · SEC XBRL · Yahoo prices
                                                      │
               Ingestion: EDGAR → parse sections → chunk → embed (bge-small) → Postgres
```

## Quick start

Prerequisites: Docker, [uv](https://docs.astral.sh/uv/), Node 22.

```bash
cp .env.example .env                  # set SEC_USER_AGENT (with a contact email) and OPENAI_API_KEY
docker compose up -d --wait postgres redis   # Redis 8: vector search for the cache

cd backend
uv sync
uv run alembic upgrade head
uv run python -m app.rag.ingest NVDA AMD --forms 10-K --limit 2   # pulls live filings from SEC EDGAR
uv run uvicorn app.main:app --reload                               # http://localhost:8000/docs
#   (Windows: add --loop asyncio:SelectorEventLoop, which the psycopg checkpointer needs)
curl "localhost:8000/search?q=China+export+controls&tickers=NVDA"  # cited, reranked passages
curl -N -X POST localhost:8000/research -H 'content-type: application/json'   -d '{"question": "Compare NVIDIA and AMD data-center risks and margins"}'   # agents, ~$0.04

cd ../frontend
npm install && npm run dev                                         # http://localhost:3000
npm test                                                           # vitest: SSE parser + UI state
```

## Research UI

The Next.js app (`frontend/`) is designed as an auditor's workpaper:
- The **agent trace** shows each step live as it streams over SSE, with the parallel data
  agents, per-step cost and timing, and a token counter while the analyst writes.
- The **report** puts a red-pencil tick beside every claim the critic verified and a "?"
  beside any it couldn't. Numbered citations open the exact 10-K passage, with a link to
  the filing on EDGAR.
- **Figure chips** show the SEC XBRL numbers a claim uses, and a small-multiples chart
  plots each company's margins.
- **Sign-off:** approve, edit the wording (citations stay attached), or reject. Signed-off
  reports get a stamp and appear under Signed-off reports.
- **Reopening a run:** each run has its own URL (`/research/<id>`), restored from the
  Postgres checkpoint, so a refresh or a server restart mid-review doesn't lose the review.

## Production concerns

| Concern | What it does | How it was checked |
|---|---|---|
| **Prompt injection** | A local classifier ([`protectai/deberta-v3-base-prompt-injection-v2`](https://huggingface.co/protectai/deberta-v3-base-prompt-injection-v2), free, CPU) screens the question *and* every retrieved 10-K passage. Flagged questions get a 400; flagged passages are dropped before the analyst sees them. | Scored all 4,361 ingested passages: 1 false positive at threshold 0.9, **none at 0.99** (the default). A planted "ignore your instructions" passage was retrieved at rank 8 of 30 and dropped (score 0.99995). |
| **PII** | Emails, phone numbers, SSNs and Luhn-valid card numbers are redacted from questions before they reach the LLM or the database. Long financial figures are left alone. | Unit tests |
| **Audit log** | `audit_log` records blocked questions, dropped passages, redactions, budget stops, and every approve/edit/reject. | Integration test on Postgres |
| **Semantic cache** | Signed-off reports are cached in Redis 8 vector search (local bge embeddings). A question ≥ 0.92 cosine-similar to one already approved returns that report for $0, with a "Run fresh anyway" option. Only human-approved answers are ever reused. | A close rephrase scored 0.996 (hit). "NVIDIA *and Intel*" vs "NVIDIA *and AMD*" scored 0.865 (miss). That near-miss is why the threshold isn't lower. |
| **Cost and latency** | `research_runs` stores cost, total latency and per-agent timings for every run. `/metrics` shows spend, p50/p95 time to review, cache-hit rate, guardrail counts and the latest eval scores. | Live run: NFLX question $0.022, 60s (analyst 28s, critic 13s, filings 15s including passage screening) |
| **Tracing** | Langfuse Cloud: one trace per run, a span per agent, and a generation per LLM call with tokens and cost. Off until `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` are set. | Unit-tested against the SDK's interface; not yet run against a live Langfuse project |

![Metrics page: runs, guardrails, sign-off counts](docs/metrics.jpg)

## Agents

`POST /research` streams the run as server-sent events (node started/finished, tokens,
per-call cost) and pauses at `awaiting_approval` with the draft report. `POST
/research/{thread_id}/resume` with `approve`, `edit` (your edited report) or `reject` finishes
it. Approved reports land in `GET /reports`.

- **Supervisor** (`gpt-5-mini`) turns the question into a plan: tickers (only ones that
  are ingested), search queries, and whether financials or prices are needed.
- **Filings** and **market** agents run **in parallel**, and neither calls an LLM. Both
  reach data **only through the MCP server**: in-process by default, or over HTTP to a
  separately running server (`MCP_URL`). A failing tool degrades the report ("price data
  unavailable") instead of failing it.
- **Analyst** (`gpt-5`) writes a structured report (Pydantic schema). Every claim cites
  passage IDs and/or table figures.
- **Critic** first runs free, deterministic checks: every cited passage must actually have
  been retrieved, and every figure must match the SEC XBRL table. Then a `gpt-5-mini` judge
  checks the summary and each claim's wording against its sources. Unsupported claims go back
  to the analyst (at most 2 revisions); any left over are shown to the reviewer as warnings.
- **Human review** is a LangGraph `interrupt()`. State is checkpointed in Postgres, so a run
  can be approved after a server restart.

Every LLM call goes through a cost tracker with a hard per-run budget (`RESEARCH_BUDGET_USD`,
default $0.10). The demo question ("Compare NVIDIA and AMD's data-center risk factors and
margins") costs **$0.03-0.04** and takes ~95s on a CPU laptop. Most of that time is 6
reranked searches plus the `gpt-5` call.

**What the critic is for, from a real run:** the first live report said NVIDIA's margins
"expanded" while citing exact figures showing they *fell* (75.0% → 71.1%). The numeric check
passed because the numbers were right, and the first version of the critic never judged
figure-only claims. Now the judge checks every claim's wording and the summary, and a
regression test covers this case.

### Answer quality

The full agent graph ran on 8 golden questions (`evals/run_answer_evals.py`; **$0.12** total
including the judge):

| Metric | Value |
|---|---|
| Evidence hit: the question's source passage was retrieved by the agent | 0.88 |
| Citation hit: the report cites that passage | 0.88 |
| Faithfulness: summary + claims supported by their sources (47 judged) | 1.00 |
| Mean cost / latency per question | $0.015 / 39s |

Honest reading: 8 questions is a smoke test, not a benchmark. Faithfulness is judged by a
small model (`gpt-5-mini`), so 1.00 means "no disagreement found", not proof. The one miss
(AMZN) is a retrieval miss: the source passage was never retrieved, so no prompt could cite
it.

## Retrieval quality

25 analyst-style questions generated by `gpt-5-mini` (total cost: **$0.006**), each tied to the
10-K passage that answers it. Searches are filtered to the question's company.

| Config | Hit@5 | MRR@10 | Latency / query (CPU) |
|---|---|---|---|
| vector (bge-small) | 0.72 | 0.57 | 0.03s |
| hybrid: vector + Postgres full-text, RRF | 0.80 | 0.61 | 0.06s |
| hybrid + cross-encoder rerank (bge-reranker-base) | **0.84** | **0.62** | 3.82s |

Honest reading: hybrid search is a clear, nearly free win. Reranking adds one more hit out of
25 (+0.04) for roughly 60× the latency on CPU, which is suggestive but not significant at this
sample size. Reproduce with `uv run python -m evals.run_retrieval_evals` (no API calls). A CI
threshold (`evals/thresholds.toml`) guards against regressions.

## MCP server

The same retrieval and financials tools are exposed over the Model Context Protocol, so any
MCP client (Claude Desktop, Cursor, the MCP Inspector, or FinSight's own agents) can use them:
`search_filings`, `get_chunk`, `list_filings`, `get_financials` (revenue and margins from SEC XBRL),
`get_price_history` (monthly closes via Yahoo Finance).

```json
{
  "mcpServers": {
    "finsight": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/finsight/backend", "python", "-m", "app.mcp.server"]
    }
  }
}
```

Try it without a client: `npx @modelcontextprotocol/inspector uv run python -m app.mcp.server`
(from `backend/`). For agents over HTTP: `python -m app.mcp.server --http --port 8001`.

## Roadmap

- [x] **Week 1: Foundations.** Monorepo, Docker Compose, async EDGAR client (rate-limited),
      10-K/10-Q section parser, paragraph-aware chunker, local embeddings, pgvector + tsvector schema, CI
- [x] **Week 2: RAG quality and MCP.** Hybrid search (RRF) + cross-encoder rerank with citations,
      MCP server (4 tools), XBRL financials, golden set + retrieval eval, Postgres integration tests in CI
- [x] **Week 3: Agents.** LangGraph supervisor → parallel filings/market → analyst ⇄ critic →
      human approval (`interrupt`, Postgres checkpoints), SSE streaming, agents as MCP clients,
      per-run cost cap, answer eval
- [x] **Week 4: UI and production.** Research UI (live agent trace, cited report, charts,
      edit and sign-off, reopen from checkpoint); injection and PII guardrails, audit log,
      semantic cache, run metrics page, Langfuse tracing
- [ ] **Week 5: Ship.** Eval-gated CI, deployment (Vercel + Fly.io + Neon), demo video

## Tech stack

Python 3.12+, FastAPI, SQLAlchemy (async), Alembic, Postgres + pgvector, sentence-transformers
(bge embeddings + cross-encoder reranker), OpenAI (GPT, structured outputs), MCP (Python SDK v2),
LangGraph · Next.js, TypeScript, Tailwind · Docker, GitHub Actions
