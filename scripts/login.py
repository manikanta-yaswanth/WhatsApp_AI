"""Open WhatsApp Web in a visible browser, wait for the QR scan, and keep the session in the profile dir.

Usage: uv run python scripts/login.py [--timeout 300]
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import get_settings  # noqa: E402
from app.scraper.auth import LoginState, open_whatsapp, wait_for_state  # noqa: E402
from app.scraper.browser import BrowserManager  # noqa: E402
from app.utils.logging import configure_logging  # noqa: E402


async def main(timeout: int) -> int:
    configure_logging()
    settings = get_settings()
    async with BrowserManager(settings, headless=False) as bm:
        page = await bm.get_page()
        state = await open_whatsapp(page, settings.whatsapp_url, settings.whatsapp_load_timeout_s)
        if state == LoginState.LOGGED_IN:
            print("Already logged in. Session is stored in", settings.profile_path)
            return 0
        print("Scan the QR code in the browser window: WhatsApp > Settings > Linked devices > Link a device")
        state = await wait_for_state(page, timeout, {LoginState.LOGGED_IN})
        if state != LoginState.LOGGED_IN:
            print("Timed out waiting for login.")
            return 1
        await asyncio.sleep(10)  # let WhatsApp finish writing keys to IndexedDB
        print("Logged in. Session stored in", settings.profile_path)
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=300)
    raise SystemExit(asyncio.run(main(parser.parse_args().timeout)))
