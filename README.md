# FinSight — Multi-Agent Financial Research Copilot

Ask a question like *"Compare NVIDIA and AMD's data-center risk factors and margins over the
last 2 years"* and a team of AI agents researches SEC filings and market data, writes a cited
analyst brief, fact-checks itself, and waits for your approval.

> Status: **Week 3 of 5**. The agent pipeline works end to end over the API (SSE): supervisor,
> parallel filings and market agents, analyst ⇄ critic fact-checking, and human approval, all
> over 24 real 10-Ks (12 companies, ~4.4k passages). A research report costs about 1-4 cents.
> The UI is next. See the roadmap below.

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
docker compose up -d --wait postgres redis

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
```

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
- [ ] **Week 4: UI and production.** Research UI with live agent trace, guardrails, Langfuse, cost tracking, semantic cache
- [ ] **Week 5: Ship.** Eval-gated CI, deployment (Vercel + Fly.io + Neon), demo video

## Tech stack

Python 3.12+, FastAPI, SQLAlchemy (async), Alembic, Postgres + pgvector, sentence-transformers
(bge embeddings + cross-encoder reranker), OpenAI (GPT, structured outputs), MCP (Python SDK v2),
LangGraph · Next.js, TypeScript, Tailwind · Docker, GitHub Actions
