"""Validated extraction -> atomic SQL upserts -> retention."""

import asyncio
import time
import uuid
from typing import Protocol

from Models.records import ScrapeRun
from Models.schema import ScrapedConversation
from scraper.whatsapp import ScrapeOutput, WhatsAppScraper
from utils.database import DatabaseUtil, SQLSession
from utils.logging import get_logger
from utils.repositories import ContactRepository, MessageRepository, RunRepository
from utils.settings import Settings
from utils.time import utcnow

log = get_logger(__name__)


def persist_conversations(session: SQLSession, conversations: list[ScrapedConversation], keep: int) -> dict[str, int]:
    contacts = ContactRepository(session)
    messages = MessageRepository(session)
    stats = {"contacts_saved": 0, "messages_saved": 0, "messages_pruned": 0}
    for conv in conversations:
        cid, inserted = contacts.upsert(conv.contact)
        stats["contacts_saved"] += int(inserted)
        contacts.upsert_conversation(cid, conv.unread_count, conv.last_message_at)
        stats["messages_saved"] += messages.insert_new(cid, conv.newest(keep))
        stats["messages_pruned"] += messages.prune(cid, keep)
    return stats


class Scraper(Protocol):
    async def scrape(self) -> ScrapeOutput: ...


class ScrapeService:
    def __init__(self, settings: Settings, database: DatabaseUtil, scraper: Scraper | None = None) -> None:
        self.settings = settings
        self.database = database
        self.scraper = scraper or WhatsAppScraper(settings)

    def create_run(self) -> ScrapeRun:
        with self.database.transaction() as session:
            return RunRepository(session).create_scrape()

    async def execute(self, run_id: uuid.UUID) -> ScrapeRun:
        start = time.perf_counter()

        def mark_running() -> ScrapeRun:
            with self.database.transaction() as session:
                repo = RunRepository(session)
                repo.mark_scrape_running(run_id)
                return repo.get_scrape(run_id)

        run = await asyncio.to_thread(mark_running)
        try:
            output = await self.scraper.scrape()

            def save() -> ScrapeRun:
                with self.database.transaction() as session:
                    stats = persist_conversations(
                        session, output.result.conversations, self.settings.max_messages_per_contact
                    )
                    run.extraction_method = output.method
                    run.contacts_found = output.contacts_found
                    run.messages_found = output.messages_found
                    run.invalid_records = output.result.invalid
                    for name, value in stats.items():
                        setattr(run, name, value)
                    run.status = "completed"
                    run.completed_at = utcnow()
                    run.duration_ms = int((time.perf_counter() - start) * 1000)
                    RunRepository(session).save_scrape(run)
                return run

            run = await asyncio.to_thread(save)
        except Exception as exc:  # noqa: BLE001 - rollback data and record the failed run
            error_name = type(exc).__name__

            def fail() -> ScrapeRun:
                with self.database.transaction() as session:
                    repo = RunRepository(session)
                    failed = repo.get_scrape(run_id)
                    failed.status = "failed"
                    failed.error_message = error_name
                    failed.completed_at = utcnow()
                    failed.duration_ms = int((time.perf_counter() - start) * 1000)
                    repo.save_scrape(failed)
                    return failed

            run = await asyncio.to_thread(fail)
        log.info(
            "scrape_finished",
            run_id=str(run.id),
            status=run.status,
            contacts_saved=run.contacts_saved,
            messages_saved=run.messages_saved,
        )
        return run
