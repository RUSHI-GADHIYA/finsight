from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from redis.asyncio import Redis

from app.agents.cache import RedisSemanticCache
from app.agents.graph import build_graph, checkpoint_serde
from app.agents.runner import ResearchService
from app.agents.store import PostgresReportStore
from app.api import ingest, metrics, research, search
from app.config import get_settings
from app.guardrails.injection import default_guard
from app.observability import tracing
from app.observability.ops import PostgresOpsLog
from app.rag.embed import get_embedder


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url)
    cache = (
        RedisSemanticCache(
            redis,
            get_embedder().embed_query,
            dim=settings.embedding_dim,
            threshold=settings.cache_similarity,
            ttl_days=settings.cache_ttl_days,
        )
        if settings.cache_enabled
        else None
    )
    # Checkpoints make interrupt()/resume work across requests (and server restarts).
    async with AsyncPostgresSaver.from_conn_string(
        settings.checkpoint_database_url, serde=checkpoint_serde()
    ) as checkpointer:
        await checkpointer.setup()  # creates/migrates LangGraph's own tables
        reports = PostgresReportStore()
        ops = PostgresOpsLog()
        app.state.reports = reports
        app.state.ops = ops
        app.state.research = ResearchService(
            graph=build_graph(checkpointer),
            store=reports,
            ops=ops,
            cache=cache,
            guard=default_guard(),  # loads the local classifier once (~1s after download)
        )
        yield
    tracing.flush()
    await redis.aclose()


app = FastAPI(title="FinSight API", version="0.5.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["content-type"],
)
app.include_router(ingest.router)
app.include_router(search.router)
app.include_router(research.router)
app.include_router(metrics.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
