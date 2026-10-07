"""Tests run against a throwaway Postgres (TEST_DATABASE_URL), never the real database."""

import os
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config.settings import Settings
from app.db.models import Base

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:55432/whatsapp_ai_test"
)
if "test" not in TEST_DATABASE_URL.rsplit("/", 1)[-1]:
    raise RuntimeError("TEST_DATABASE_URL must point at a database whose name contains 'test'")


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(database_url=TEST_DATABASE_URL, openai_api_key=None, api_key=None, _env_file=None)


@pytest.fixture(scope="session")
async def engine():  # type: ignore[no-untyped-def]
    eng = create_async_engine(TEST_DATABASE_URL)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:  # type: ignore[no-untyped-def]
    yield async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))


Responder = Callable[[list[BaseMessage], list[str]], AIMessage]


class ScriptedChatModel(BaseChatModel):
    """Deterministic chat model: a responder decides each reply from the messages and bound tool names."""

    responder: Any
    bound_tools: list[str] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedChatModel":  # type: ignore[override]
        names = []
        for t in tools:
            names.append(getattr(t, "name", None) or getattr(t, "__name__", None) or t.get("name"))
        return self.model_copy(update={"bound_tools": names})

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kw: Any
    ) -> ChatResult:
        msg = self.responder(messages, self.bound_tools)
        return ChatResult(generations=[ChatGeneration(message=msg)])


def tool_call(name: str, args: dict[str, Any] | None = None) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args or {}, "id": f"call_{uuid.uuid4().hex[:8]}"}],
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )


@pytest.fixture
def scripted() -> Callable[[Responder], ScriptedChatModel]:
    return lambda responder: ScriptedChatModel(responder=responder)
