import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_scrape_service
from app.db.database import get_session
from app.db.repositories.scrape_repository import ScrapeRepository
from app.schemas.scrape import ScrapeRunOut, ScrapeStartResponse
from app.services.scrape_service import ScrapeService

router = APIRouter(prefix="/scrape", tags=["scraping"])


@router.post("/start", response_model=ScrapeStartResponse, status_code=202)
async def start_scrape(service: ScrapeService = Depends(get_scrape_service)) -> ScrapeStartResponse:
    run = await service.create_run()
    service.start_in_background(run.id)
    return ScrapeStartResponse(run_id=run.id, status="started")


@router.get("/runs", response_model=list[ScrapeRunOut])
async def list_runs(limit: int = 20, session: AsyncSession = Depends(get_session)) -> list:
    return await ScrapeRepository(session).latest(min(limit, 100))


@router.get("/{run_id}", response_model=ScrapeRunOut)
async def get_run(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> object:
    run = await ScrapeRepository(session).get(run_id)
    if run is None:
        raise HTTPException(404, "scrape run not found")
    return run
