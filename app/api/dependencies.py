import secrets
from functools import lru_cache

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.config.settings import Settings, get_settings
from app.db.database import get_session_factory
from app.llm.client import LLMNotConfiguredError, build_chat_model
from app.services.agent_service import AgentService
from app.services.scrape_service import ScrapeService

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(key: str | None = Security(_api_key_header), settings: Settings = Depends(get_settings)) -> None:
    expected = settings.api_key.get_secret_value() if settings.api_key else ""
    if expected and not (key and secrets.compare_digest(key, expected)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing X-API-Key")


@lru_cache
def get_scrape_service() -> ScrapeService:
    return ScrapeService(get_settings(), get_session_factory())


@lru_cache
def _agent_service() -> AgentService:
    s = get_settings()
    return AgentService(s, get_session_factory(), build_chat_model(s), build_chat_model(s, judge=True))


def get_agent_service() -> AgentService:
    try:
        return _agent_service()
    except LLMNotConfiguredError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
