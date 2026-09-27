"""Agent research over SSE, plus approved-report history.

POST /research                       {"question": ...}          -> SSE stream
POST /research/{thread_id}/resume    {"action": "approve"|"edit"|"reject", "report"?}
GET  /research/{thread_id}           status (+ the pending review while paused)
GET  /chunks/{chunk_id}              one cited passage
GET  /reports, GET /reports/{id}
"""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Literal, Self

from fastapi import APIRouter, Depends, HTTPException, Request
from langgraph.types import Command
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette import EventSourceResponse

from app.agents.runner import Event, ResearchService, RunStatus
from app.agents.state import Report
from app.agents.store import PostgresReportStore, ReportOut
from app.db.session import get_session
from app.rag import retrieval
from app.rag.retrieval import RetrievedChunk

router = APIRouter(tags=["research"])


def get_research(request: Request) -> ResearchService:
    service: ResearchService = request.app.state.research
    return service


def get_reports(request: Request) -> PostgresReportStore:
    store: PostgresReportStore = request.app.state.reports
    return store


Service = Annotated[ResearchService, Depends(get_research)]
Reports = Annotated[PostgresReportStore, Depends(get_reports)]


class ResearchRequest(BaseModel):
    question: str = Field(min_length=5, max_length=1000)


class ResumeRequest(BaseModel):
    action: Literal["approve", "edit", "reject"]
    report: Report | None = None  # required for "edit"

    @model_validator(mode="after")
    def _edit_needs_report(self) -> Self:
        if self.action == "edit" and self.report is None:
            raise ValueError("action 'edit' requires the edited report")
        return self


async def _sse(events: AsyncIterator[Event]) -> AsyncIterator[dict[str, str]]:
    async for e in events:
        yield {"event": e["event"], "data": json.dumps(e["data"], default=str)}


@router.post("/research")
async def research(body: ResearchRequest, service: Service) -> EventSourceResponse:
    thread_id = uuid.uuid4().hex
    return EventSourceResponse(_sse(service.stream(thread_id, {"question": body.question})))


@router.post("/research/{thread_id}/resume")
async def resume(thread_id: str, body: ResumeRequest, service: Service) -> EventSourceResponse:
    if not await service.awaiting_approval(thread_id):
        raise HTTPException(409, "This research run is not waiting for approval")
    decision = body.model_dump(mode="json", exclude_none=True)
    return EventSourceResponse(_sse(service.stream(thread_id, Command(resume=decision))))


@router.get("/research/{thread_id}")
async def research_status(thread_id: str, service: Service) -> RunStatus:
    status = await service.run_status(thread_id)
    if status is None:
        raise HTTPException(404, f"No research run {thread_id}")
    return status


@router.get("/chunks/{chunk_id}")
async def get_chunk(
    chunk_id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> RetrievedChunk:
    chunk = await retrieval.get_chunk(session, chunk_id)
    if chunk is None:
        raise HTTPException(404, f"No chunk {chunk_id}")
    return chunk


@router.get("/reports")
async def list_reports(reports: Reports) -> list[ReportOut]:
    return await reports.recent()


@router.get("/reports/{report_id}")
async def get_report(report_id: int, reports: Reports) -> ReportOut:
    found = await reports.get(report_id)
    if found is None:
        raise HTTPException(404, f"No report {report_id}")
    return found
