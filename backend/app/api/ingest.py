from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel, Field

from app.rag.ingest import ingest_tickers

router = APIRouter(prefix="/ingest", tags=["ingest"])


class IngestRequest(BaseModel):
    tickers: list[str] = Field(min_length=1, max_length=20)
    forms: list[str] = ["10-K"]
    limit: int = Field(default=2, ge=1, le=8)


@router.post("", status_code=202)
async def start_ingest(req: IngestRequest, background: BackgroundTasks) -> dict[str, str]:
    background.add_task(
        ingest_tickers, [t.upper() for t in req.tickers], tuple(req.forms), req.limit
    )
    return {"status": "accepted"}
