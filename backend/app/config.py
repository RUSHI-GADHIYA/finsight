from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    openai_api_key: str = ""
    # Model names are config, not code: fast tier for routing/extraction/critic,
    # smart tier only for final report synthesis.
    llm_fast_model: str = "gpt-5-mini"
    llm_smart_model: str = "gpt-5"
    # USD per 1M tokens as (input, output), used by the cost tracker / budget guard.
    # Verify against https://openai.com/api/pricing and override via env (JSON) if changed.
    llm_prices_per_mtok: dict[str, tuple[float, float]] = {
        "gpt-5-mini": (0.25, 2.00),
        "gpt-5": (1.25, 10.00),
    }

    sec_user_agent: str = "FinSight research-demo contact@example.com"

    database_url: str = "postgresql+asyncpg://finsight:finsight@localhost:5432/finsight"
    redis_url: str = "redis://localhost:6379/0"

    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    reranker_model: str = "BAAI/bge-reranker-base"

    chunk_size_chars: int = 1500
    chunk_overlap_chars: int = 200

    # Agents reach data through the MCP server: in-process by default, or over streamable
    # HTTP when set (e.g. http://127.0.0.1:8001/mcp with `python -m app.mcp.server --http`).
    mcp_url: str | None = None
    research_budget_usd: float = 0.10  # hard cap per research run, all agents combined
    max_revisions: int = 2  # analyst <-> critic loops before the report goes to the human
    # Browser origins allowed to call the API (the Next.js UI calls FastAPI directly, so
    # SSE streams aren't buffered by a proxy).
    cors_origins: list[str] = ["http://localhost:3000"]

    # Guardrails: local prompt-injection classifier on the question and retrieved passages.
    guardrails_enabled: bool = True
    injection_model: str = "protectai/deberta-v3-base-prompt-injection-v2"
    # Probability of the INJECTION label. Measured 2026-09-27 on all 4,361 ingested passages:
    # 1 false positive at 0.9, none at 0.99; planted injections scored > 0.9999.
    injection_threshold: float = 0.99

    # Semantic cache of human-approved reports (Redis vector search).
    cache_enabled: bool = True
    cache_similarity: float = 0.92  # cosine similarity of question embeddings
    cache_ttl_days: int = 7

    # Langfuse Cloud tracing; off unless both keys are set.
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = "https://cloud.langfuse.com"

    @property
    def checkpoint_database_url(self) -> str:
        """LangGraph's Postgres checkpointer uses psycopg, which wants a plain libpq URL."""
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
