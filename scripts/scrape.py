"""Run one scrape synchronously from the CLI (same pipeline as POST /api/v1/scrape/start).

Usage: uv run python scripts/scrape.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import get_settings  # noqa: E402
from app.db.database import get_engine, get_session_factory  # noqa: E402
from app.schemas.scrape import ScrapeRunOut  # noqa: E402
from app.services.scrape_service import ScrapeService  # noqa: E402
from app.utils.logging import configure_logging  # noqa: E402


async def main() -> int:
    configure_logging()
    service = ScrapeService(get_settings(), get_session_factory())
    run = await service.create_run()
    run = await service.execute(run.id)
    print(json.dumps(ScrapeRunOut.model_validate(run).model_dump(mode="json"), indent=2))
    await get_engine().dispose()
    return 0 if run.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
