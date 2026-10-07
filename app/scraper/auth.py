import asyncio
from enum import StrEnum

from playwright.async_api import Page

from app.utils.logging import get_logger

log = get_logger(__name__)

# Several alternatives per state: WhatsApp changes its markup often.
CHAT_LIST_SELECTORS = ["#pane-side", "[aria-label='Chat list']", "div[data-testid='chat-list']"]
QR_SELECTORS = ["canvas[aria-label*='Scan']", "div[data-ref] canvas", "[data-testid='qrcode']", "canvas"]


class LoginState(StrEnum):
    LOGGED_IN = "logged_in"
    QR_REQUIRED = "qr_required"
    LOADING = "loading"


async def _any_visible(page: Page, selectors: list[str]) -> bool:
    for sel in selectors:
        try:
            if await page.locator(sel).first.is_visible(timeout=200):
                return True
        except Exception:  # noqa: BLE001 - selector/locator errors just mean "not this one"
            continue
    return False


async def detect_state(page: Page) -> LoginState:
    if await _any_visible(page, CHAT_LIST_SELECTORS):
        return LoginState.LOGGED_IN
    if await _any_visible(page, QR_SELECTORS):
        return LoginState.QR_REQUIRED
    return LoginState.LOADING


async def wait_for_state(page: Page, timeout_s: int, want: set[LoginState]) -> LoginState:
    deadline = asyncio.get_running_loop().time() + timeout_s
    state = LoginState.LOADING
    while asyncio.get_running_loop().time() < deadline:
        state = await detect_state(page)
        if state in want:
            return state
        await asyncio.sleep(1)
    return state


async def open_whatsapp(page: Page, url: str, timeout_s: int) -> LoginState:
    await page.goto(url, wait_until="domcontentloaded")
    state = await wait_for_state(page, timeout_s, {LoginState.LOGGED_IN, LoginState.QR_REQUIRED})
    log.info("whatsapp_opened", state=state.value)
    return state
