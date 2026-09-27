# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

FinSight is a portfolio app for an AI Engineer job search. It is a multi-agent financial research copilot built on SEC filings. The 5-week roadmap in `README.md` is complete; the repo is not deployed publicly (a one-command local Docker stack instead). Keep the recruiter-facing goals in mind: agents (LangGraph), MCP, hybrid RAG, evals, observability, guardrails, and full-stack delivery.

**The user's cost rule:** keep OpenAI spend minimal (Week 2 total was under $0.01). Any script that calls an LLM must go through `app.observability.cost.CostTracker` with a hard `--budget-usd` cap, and must print its actual spend. Prefer local/free options (embeddings, reranker and retrieval evals are all local).

## Commands

Infrastructure (from the repo root): `docker compose up -d --wait postgres redis`. The full stack (`migrate` → `seed` → `backend` → `frontend`) is behind the `app` profile: `docker compose --profile app up --build`.
- **Corpus snapshot:** `backend/seed/finsight-seed.dump` (filings, chunks, signed-off reports; `pg_dump -Fc`, needs `pg_restore` 17). `backend/scripts/restore_seed.sh` restores it into an empty migrated DB (used by the `seed` service and the Evals workflow). After re-ingesting, re-export it with `backend/scripts/export_seed.sh` and regenerate the golden set, because chunk IDs change.
- **Line endings:** `.gitattributes` forces LF, because scripts and Dockerfiles run in Linux containers even from a Windows clone.

Backend (run from `backend/`; uv manages the venv):
- `uv sync`: install. torch comes from the CPU-only index configured in `pyproject.toml`.
- `uv run pytest -q`; a single test: `uv run pytest tests/test_parser.py::test_name`. `tests/integration/` needs Postgres and skips itself if it's unreachable.
- `uv run ruff check . && uv run ruff format --check . && uv run mypy app tests evals` (mypy is `strict`)
- `uv run alembic upgrade head`
- `uv run python -m app.rag.ingest NVDA AMD --forms 10-K --limit 2 [--reingest]`: live EDGAR ingestion. It skips accessions already stored unless `--reingest` is given.
- `uv run uvicorn app.main:app --reload`: `/search`, `/filings`, `/ingest`, `/research` (SSE), `/reports`, `/docs`. On **Windows** add `--loop asyncio:SelectorEventLoop`: psycopg (the LangGraph checkpointer) can't run on the default Proactor loop. `tests/conftest.py` does the same for pytest.
- Research over SSE: `curl -N -X POST localhost:8000/research -H 'content-type: application/json' -d '{"question": "..."}'`, then `POST /research/{thread_id}/resume` with `{"action": "approve" | "edit" | "reject", "report"?}`. **Costs API credits** (capped per run by `RESEARCH_BUDGET_USD`, default $0.10).
- `uv run python -m app.mcp.server [--http --port 8001]`: MCP server (stdio by default)
- `uv run python -m evals.run_retrieval_evals`: free retrieval eval. It writes `evals/results/retrieval.md`/`.json` and fails below `evals/thresholds.toml`. `--gated-only` (used by the CI **Evals** workflow) runs just hybrid+rerank and doesn't overwrite the committed results. `--thresholds FILE` lets you prove the gate fails.
- `uv run python -m evals.generate_golden --force`: **costs API credits**. It regenerates `evals/golden_set.jsonl`; only rerun it deliberately.
- `uv run python -m app.llm`: checks the OpenAI key and that the configured models exist.
- `uv run python -m app.agents.cache`: backfills the semantic cache from approved reports (free, local embeddings); run it after a Redis reset.
- `uv run python -m evals.run_answer_evals --n 8 --budget-usd 0.50`: **costs API credits**. It runs the full agent graph on golden questions and writes `evals/results/answers.md`/`.json`. In CI it only runs via the manual **Answer evals** workflow (needs an `OPENAI_API_KEY` secret).

Frontend (from `frontend/`): `npm run dev | lint | test | build` (vitest covers the pure `lib/` logic). It runs **Next.js 16**, which is newer than most training data. Follow `frontend/AGENTS.md` and read `frontend/node_modules/next/dist/docs/` before writing frontend code.

## Architecture notes

- **Ingestion** (`app/rag/`): `edgar_client` → `parser` → `chunker` → `embed` → `ingest`.
  - The parser is form-agnostic, with three rules: (1) only headings whose title is a known 10-K/10-Q item title count, so running page headers like "Item 1A" are ignored; (2) repeats of the same section merge; (3) keep the **longest** span per section, which skips the table of contents. Filers without `Item N.` headings (Intel) fall back to standalone title lines.
  - `html_to_text` inserts newlines only after block elements. Inline spans must stay joined (MSFT splits "RIS"+"K FACTORS").
  - Section keys (`risk_factors`, `mdna`, `business`, `market_risk`, `legal_proceedings`) are the citation vocabulary. JPM has no MD&A because it lives in an exhibit, which isn't ingested.
  - `EdgarClient.list_filings` pages into `filings.files`: heavy filers (banks) push older 10-Ks out of `filings.recent`.
- **Retrieval** (`app/rag/retrieval.py`): vector search (HNSW, with `hnsw.iterative_scan` so filtered queries still return enough rows) + Postgres full-text (terms OR-ed, `ts_rank_cd`) → RRF → optional cross-encoder rerank (`rerank.py`, pool of 30). The keyword half is Postgres full-text, **not** BM25; describe it accurately. Rerank costs ~4s/query on CPU.
- **Financials** (`app/rag/financials.py`): figures come from SEC XBRL company facts, never from the LLM. Full-year 10-K facts only, the latest filing wins, and tag fallbacks cover companies that switched XBRL tags.
- **MCP** (`app/mcp/server.py`): mcp SDK **v2**. That means `MCPServer`, not `FastMCP`; tests use `mcp.Client(server)` in-process. Raise `ToolError` for expected failures; any other exception is logged as a crash and its message is hidden from the client.
- **Golden set is keyed by `chunk_id`.** `--reingest` assigns new chunk IDs, so after re-ingesting, regenerate the golden set (a few cents) or the retrieval eval becomes meaningless.
- **Embedding dimension is 384 in three places.** They must change together: `settings.embedding_dim`, `EMBEDDING_DIM` in `db/models.py`, and the `0001` migration. Migrations are hand-written; review autogenerate output, which doesn't handle `Computed`/HNSW well.
- **BGE query prefix:** `embed_query` adds a retrieval instruction prefix and `embed_documents` does not. Always embed queries through `embed_query`.
- **SEC fair access:** EDGAR requires a `User-Agent` with contact info (`SEC_USER_AGENT`) and at most 10 req/s. `EdgarClient` rate-limits to 8/s. Tests use `httpx.MockTransport` and never hit the network.
- **LLM provider is OpenAI.** Get clients only through `app.llm.get_openai_client()`. Never hard-code model names: use `settings.llm_fast_model` (routing, extraction, critic) or `settings.llm_smart_model` (final synthesis only). Per-model prices live in `settings.llm_prices_per_mtok`. Embeddings are deliberately local (bge), not OpenAI.
- **Agents** (`app/agents/`): a LangGraph graph, `supervisor → (filings ∥ market) → analyst ⇄ critic → human_review`.
  - Nodes get run-scoped deps (`AgentDeps`: tools, llm, store, thread_id) as LangGraph **runtime context**, so tests inject fakes (`tests/agent_fakes.py`). No test needs a key or network.
  - Agents reach data **only through MCP** (`tools.py`): in-process `mcp.Client(server)` by default, or streamable HTTP when `MCP_URL` is set.
  - LLM calls go through `agents/llm.py` (`responses.stream` + Pydantic schema + `CostTracker.record`), not langchain-openai. Only supervisor (fast), analyst (smart) and critic (fast) call the LLM.
  - The critic runs free deterministic checks first: cited `chunk_id`s must have been retrieved, and figures must match the XBRL table. It then asks an LLM judge about the summary (claim 0) and every claim's wording. The judge must also check figure-only claims: correct numbers with the wrong direction ("expanded" when margins fell) happened in a live run.
  - Routing uses `Command(goto=...)`. `interrupt()` in `human_review` pauses for approval, and on resume the node re-runs from the top.
- **Checkpoints:** `AsyncPostgresSaver` in the app, `InMemorySaver` in tests. Both use `checkpoint_serde()`, a msgpack **allowlist** built from `state.CHECKPOINT_TYPES`. **Add any new Pydantic type you put in graph state there**, or it is silently rebuilt as a plain dict on resume.
- **Frontend** (`frontend/src/`): the browser calls FastAPI directly (`NEXT_PUBLIC_API_URL`, CORS via `settings.cors_origins`), so SSE isn't buffered by a proxy.
  - `lib/sse.ts` reads `POST` SSE from `fetch`, because `EventSource` can only `GET`.
  - `lib/research.ts` is a pure reducer from events to UI state. Its test replays a real recorded run (`lib/__fixtures__/research-run.json`), so update the fixture if event payloads change.
  - Server pages call `await connection()` so they are never prerendered at build time (CI has no API).
  - The design tokens (ledger/sheet/ink/graphite/rule/pencil) live in `globals.css`. The red "pencil" is reserved for tick marks, flags and sign-off.
- **Guardrails** (`app/guardrails/`): `ResearchService.screen()` redacts PII and blocks injection-like questions (400) before a run. The `filings` node drops flagged passages and emits a `guardrail` event, which the runner writes to `audit_log`.
  - The threshold (0.99) was measured on the whole corpus. If you change the model or threshold, re-measure false positives on real passages.
  - The classifier loads at API startup (~740 MB download once). Tests use `FakeClassifier`.
- **Runner is the only writer of ops data** (`app/agents/runner.py`): `research_runs`, `audit_log`, Langfuse traces and cache writes all come from stream events there. Nodes only emit events. Ops writes are best-effort and never fail a run.
  - Langfuse uses explicit `start_observation` objects, not "current" context managers, because OTel context doesn't survive the async generator's `yield`s.
- **Semantic cache** only ever stores human-approved reports (added in `_after_review`). It needs Redis 8 (bundled query engine); `docker-compose.yml` and CI use `redis:8`.
- **Config:** `app/config.py` (pydantic-settings) reads `.env` from either `backend/` or the repo root.
