"""Tests run against a throwaway Postgres (TEST_DATABASE_URL), never the real database."""

import os
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from psycopg2 import sql
from psycopg2.extensions import parse_dsn

from utils.database import DatabaseUtil
from utils.settings import Settings

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:55432/whatsapp_ai_test"
)
TEST_CONFIG = parse_dsn(TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1))
if "test" not in TEST_CONFIG.get("dbname", ""):
    raise RuntimeError("TEST_DATABASE_URL must point at a database whose name contains 'test'")


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(database_url=TEST_DATABASE_URL, openai_api_key=None, _env_file=None)


@pytest.fixture
def database() -> Iterator[DatabaseUtil]:
    admin = DatabaseUtil(TEST_CONFIG)
    schema = "wa_test_" + uuid.uuid4().hex
    with admin.transaction() as s:
        s.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    db = DatabaseUtil({**TEST_CONFIG, "options": f"-c search_path={schema}"})
    try:
        db.initialize()
        yield db
    finally:
        with admin.transaction() as s:
            s.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


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
