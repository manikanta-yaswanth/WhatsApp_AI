from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from app.api.dependencies import require_api_key
from app.api.routes import agents, contacts, health, messages, scrape
from app.config.settings import get_settings
from app.db.database import get_engine
from app.utils.logging import configure_logging


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging(json=get_settings().app_env != "development")
    yield
    await get_engine().dispose()


app = FastAPI(title="WhatsApp Conversation Intelligence Platform", version="0.1.0", lifespan=lifespan)

API_PREFIX = "/api/v1"
app.include_router(health.router, prefix=API_PREFIX)
for module in (scrape, contacts, messages, agents):
    app.include_router(module.router, prefix=API_PREFIX, dependencies=[Depends(require_api_key)])
