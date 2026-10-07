import asyncio
import time
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import Settings
from app.db.models import ScrapeRun
from app.db.repositories.contact_repository import ContactRepository
from app.db.repositories.message_repository import MessageRepository
from app.db.repositories.scrape_repository import ScrapeRepository
from app.schemas.conversation import ScrapedConversation
from app.scraper.whatsapp import ScrapeOutput, WhatsAppScraper
from app.utils.logging import get_logger
from app.utils.time import utcnow

log = get_logger(__name__)

_background_tasks: set[asyncio.Task] = set()


async def persist_conversations(
    session: AsyncSession, conversations: list[ScrapedConversation], keep: int
) -> dict[str, int]:
    """Idempotent upsert: contacts updated in place, messages inserted once, older ones pruned."""
    contacts = ContactRepository(session)
    messages = MessageRepository(session)
    stats = {"contacts_saved": 0, "messages_saved": 0, "messages_pruned": 0}
    for conv in conversations:
        contact_id, inserted = await contacts.upsert(conv.contact)
        stats["contacts_saved"] += int(inserted)
        await contacts.upsert_conversation(contact_id, conv.unread_count, conv.last_message_at)
        stats["messages_saved"] += await messages.insert_new(contact_id, conv.newest(keep))
        stats["messages_pruned"] += await messages.prune(contact_id, keep)
    return stats


class ScrapeService:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        scraper: WhatsAppScraper | None = None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.scraper = scraper or WhatsAppScraper(settings)

    async def create_run(self) -> ScrapeRun:
        async with self.session_factory() as session, session.begin():
            return await ScrapeRepository(session).create()

    def start_in_background(self, run_id: uuid.UUID) -> None:
        task = asyncio.create_task(self.execute(run_id))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    async def execute(self, run_id: uuid.UUID) -> ScrapeRun:
        t0 = time.perf_counter()
        async with self.session_factory() as session:
            repo = ScrapeRepository(session)
            run = await repo.get(run_id)
            if run is None:
                raise ValueError(f"scrape run {run_id} not found")
            await repo.mark_running(run)
            await session.commit()
            try:
                output: ScrapeOutput = await self.scraper.scrape()
                stats = await persist_conversations(
                    session, output.result.conversations, self.settings.max_messages_per_contact
                )
                run.extraction_method = output.method
                run.contacts_found = output.contacts_found
                run.messages_found = output.messages_found
                run.invalid_records = output.result.invalid
                run.contacts_saved = stats["contacts_saved"]
                run.messages_saved = stats["messages_saved"]
                run.messages_pruned = stats["messages_pruned"]
                run.status = "completed"
            except Exception as exc:  # noqa: BLE001 - recorded on the run row
                await session.rollback()
                run = await repo.get(run_id)
                assert run is not None
                run.status = "failed"
                run.error_message = f"{type(exc).__name__}: {exc}"[:2000]
                log.error("scrape_failed", run_id=str(run_id), error=type(exc).__name__)
            run.completed_at = utcnow()
            run.duration_ms = int((time.perf_counter() - t0) * 1000)
            await session.commit()
            log.info(
                "scrape_finished",
                run_id=str(run_id),
                status=run.status,
                duration_ms=run.duration_ms,
                contacts_saved=run.contacts_saved,
                messages_saved=run.messages_saved,
            )
            return run
