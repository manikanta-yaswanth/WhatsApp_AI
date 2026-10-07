import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_agent_service
from app.config.settings import get_settings
from app.db.database import get_session
from app.db.models import Evaluation, EvaluationRun
from app.db.repositories.quality_repository import QualityRepository
from app.evaluation.judge import create_evaluation_run, run_evaluation
from app.evaluation.metrics import platform_metrics
from app.schemas.agent import (
    AgentQuery,
    AgentResponse,
    ClassifiedContact,
    ClassifyRequest,
    SummarizeRequest,
    SummarizeResponse,
)
from app.services.agent_service import AgentService, NotFoundError

router = APIRouter(tags=["ai"])


@router.post("/agents/query", response_model=AgentResponse)
async def query_agent(body: AgentQuery, service: AgentService = Depends(get_agent_service)) -> AgentResponse:
    try:
        response, _ = await service.run(body.query, judge=body.judge)
    except RuntimeError as exc:
        raise HTTPException(502, f"agent failed: {exc}") from exc
    return response


@router.post("/agents/classify", response_model=list[ClassifiedContact])
async def classify(body: ClassifyRequest, service: AgentService = Depends(get_agent_service)) -> list:
    return await service.classify(body.contact_ids, body.limit, body.only_unclassified)


@router.post("/agents/summarize", response_model=SummarizeResponse)
async def summarize(body: SummarizeRequest, service: AgentService = Depends(get_agent_service)) -> SummarizeResponse:
    try:
        return await service.summarize(body.contact_id)
    except NotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/agents/data-quality")
async def data_quality(session: AsyncSession = Depends(get_session)) -> dict:
    return await QualityRepository(session).report(get_settings().max_messages_per_contact)


@router.post("/evaluation/run", status_code=202)
async def start_evaluation(background: BackgroundTasks, service: AgentService = Depends(get_agent_service)) -> dict:
    run_id = await create_evaluation_run(service)
    background.add_task(run_evaluation, service, run_id)
    return {"evaluation_id": run_id, "status": "running"}


@router.get("/evaluation/{evaluation_id}")
async def get_evaluation(evaluation_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> dict:
    run = await session.get(EvaluationRun, evaluation_id)
    if run is None:
        raise HTTPException(404, "evaluation run not found")
    rows = (
        await session.execute(
            select(Evaluation).where(Evaluation.evaluation_run_id == evaluation_id).order_by(Evaluation.created_at)
        )
    ).scalars()
    return {
        "id": run.id,
        "status": run.status,
        "dataset": run.dataset,
        "total_questions": run.total_questions,
        "metrics": run.metrics,
        "error_message": run.error_message,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "results": [
            {
                "question": e.question,
                "answer": e.answer,
                "final_score": e.final_score,
                "correctness": e.correctness,
                "groundedness": e.groundedness,
                "hallucination": e.hallucination,
                "tool_accuracy": e.tool_accuracy,
                "tools_used": e.tools_used,
                "reasoning": e.reasoning,
            }
            for e in rows
        ],
    }


@router.get("/metrics")
async def metrics(session: AsyncSession = Depends(get_session)) -> dict:
    return await platform_metrics(session)
