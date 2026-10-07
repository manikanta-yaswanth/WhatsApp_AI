import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ScrapeRun
from app.utils.time import utcnow


class ScrapeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self) -> ScrapeRun:
        run = ScrapeRun(id=uuid.uuid4(), status="queued")
        self.session.add(run)
        await self.session.flush()
        return run

    async def get(self, run_id: uuid.UUID) -> ScrapeRun | None:
        return await self.session.get(ScrapeRun, run_id)

    async def latest(self, limit: int = 20) -> list[ScrapeRun]:
        q = select(ScrapeRun).order_by(ScrapeRun.created_at.desc()).limit(limit)
        return list((await self.session.execute(q)).scalars())

    async def last_completed(self) -> ScrapeRun | None:
        q = select(ScrapeRun).where(ScrapeRun.status == "completed").order_by(ScrapeRun.completed_at.desc()).limit(1)
        return (await self.session.execute(q)).scalar_one_or_none()

    async def mark_running(self, run: ScrapeRun) -> None:
        run.status = "running"
        run.started_at = utcnow()
        await self.session.flush()
