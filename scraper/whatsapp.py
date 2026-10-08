import asyncio
import time
from dataclasses import dataclass, field

from scraper.auth import LoginState, open_whatsapp
from scraper.browser import BrowserManager
from scraper.contacts import ContactExtractor
from scraper.messages import MessageExtractor
from scraper.parser import ParseResult, parse_dom_payload, parse_store_payload
from utils.logging import get_logger
from utils.settings import Settings

log = get_logger(__name__)

# One Chromium profile can only be opened by one browser at a time.
_browser_lock = asyncio.Lock()


class NotAuthenticatedError(RuntimeError):
    pass


@dataclass
class ScrapeOutput:
    method: str
    result: ParseResult
    contacts_found: int = 0
    messages_found: int = 0
    timings_ms: dict[str, int] = field(default_factory=dict)


class WhatsAppScraper:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.contacts = ContactExtractor()
        self.messages = MessageExtractor()

    async def scrape(self) -> ScrapeOutput:
        s = self.settings
        async with _browser_lock, BrowserManager(s) as bm:
            page = await bm.get_page()
            state = await open_whatsapp(page, s.whatsapp_url, s.whatsapp_load_timeout_s)
            if state != LoginState.LOGGED_IN:
                raise NotAuthenticatedError(
                    "WhatsApp Web is not logged in. Run `uv run python scripts/login.py` and scan the QR code."
                )
            # The model store finishes hydrating shortly after the chat list renders.
            for _ in range(20):
                if await self.contacts.store_available(page):
                    break
                await asyncio.sleep(0.5)
            if await self.contacts.store_available(page):
                return await self._scrape_store(page)
            log.warning("store_unavailable_using_dom_fallback")
            return await self._scrape_dom(page)

    async def _scrape_store(self, page) -> ScrapeOutput:  # type: ignore[no-untyped-def]
        s = self.settings
        t0 = time.perf_counter()
        chats = await self.contacts.extract_from_store(page, s.max_chats_per_scrape, s.include_groups)
        t1 = time.perf_counter()
        for chat in chats:
            try:
                chat["messages"] = await self.messages.extract_from_store(page, chat["id"], s.max_messages_per_contact)
            except Exception as exc:  # noqa: BLE001 - one broken chat must not fail the run
                log.warning("message_extraction_failed", error=type(exc).__name__)
                chat["messages"] = []
        t2 = time.perf_counter()
        result = parse_store_payload(chats, s.max_messages_per_contact)
        out = ScrapeOutput(
            method="store",
            result=result,
            contacts_found=len(chats),
            messages_found=sum(len(c["messages"]) for c in chats),
            timings_ms={"contacts_ms": int((t1 - t0) * 1000), "messages_ms": int((t2 - t1) * 1000)},
        )
        log.info(
            "whatsapp_extracted",
            method=out.method,
            contacts=out.contacts_found,
            messages_extracted=out.messages_found,
            invalid=result.invalid,
            **out.timings_ms,
        )
        return out

    async def _scrape_dom(self, page) -> ScrapeOutput:  # type: ignore[no-untyped-def]
        s = self.settings
        chats = await self.contacts.extract_from_dom(page, s.max_chats_per_scrape)
        for chat in chats:
            try:
                chat["messages"] = await self.messages.extract_from_dom(page, chat["name"], s.max_messages_per_contact)
            except Exception as exc:  # noqa: BLE001
                log.warning("message_extraction_failed", error=type(exc).__name__)
                chat["messages"] = []
        result = parse_dom_payload(chats, s.max_messages_per_contact)
        return ScrapeOutput(
            method="dom",
            result=result,
            contacts_found=len(chats),
            messages_found=sum(len(c["messages"]) for c in chats),
        )
