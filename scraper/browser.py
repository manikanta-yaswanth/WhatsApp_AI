import asyncio
from pathlib import Path

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from utils.settings import Settings

# WhatsApp Web refuses "HeadlessChrome" user agents.
_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


class BrowserManager:
    """Owns a persistent Chromium profile so the WhatsApp session survives restarts.

    WhatsApp Web keeps its keys in IndexedDB, which a persistent profile preserves
    more reliably than a storage_state JSON file.
    """

    def __init__(self, settings: Settings, headless: bool | None = None) -> None:
        self.settings = settings
        self.headless = settings.whatsapp_headless if headless is None else headless
        self._pw: Playwright | None = None
        self._context: BrowserContext | None = None

    @property
    def profile_dir(self) -> Path:
        return self.settings.profile_path

    def _clear_stale_locks(self) -> None:
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            (self.profile_dir / name).unlink(missing_ok=True)

    async def start(self) -> BrowserContext:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._clear_stale_locks()
        self._pw = await async_playwright().start()
        self._context = await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            user_agent=_USER_AGENT,
            viewport={"width": 1366, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        return self._context

    async def get_page(self) -> Page:
        if self._context is None:
            await self.start()
        assert self._context is not None
        return self._context.pages[0] if self._context.pages else await self._context.new_page()

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    async def __aenter__(self) -> "BrowserManager":
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await asyncio.shield(self.close())
