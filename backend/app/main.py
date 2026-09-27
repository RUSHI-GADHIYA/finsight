from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agents.graph import build_graph, checkpoint_serde
from app.agents.runner import ResearchService
from app.agents.store import PostgresReportStore
from app.api import ingest, research, search
from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Checkpoints make interrupt()/resume work across requests (and server restarts).
    async with AsyncPostgresSaver.from_conn_string(
        get_settings().checkpoint_database_url, serde=checkpoint_serde()
    ) as checkpointer:
        await checkpointer.setup()  # creates/migrates LangGraph's own tables
        reports = PostgresReportStore()
        app.state.reports = reports
        app.state.research = ResearchService(graph=build_graph(checkpointer), store=reports)
        yield


app = FastAPI(title="FinSight API", version="0.3.0", lifespan=lifespan)
app.include_router(ingest.router)
app.include_router(search.router)
app.include_router(research.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
